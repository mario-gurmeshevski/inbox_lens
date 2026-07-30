import logging
import threading
from datetime import datetime, timedelta, timezone

from src.scripts import cache, email_reader
from src.scripts.constants import DB_PATH
from src.scripts.event_bus import bus as event_bus

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30
MAX_ATTEMPTS = 6
BACKOFF_SCHEDULE = (60, 300, 900, 3600, 14400)  # 1m, 5m, 15m, 1h, 4h
_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"


def _next_attempt_at(attempts: int) -> str | None:
    idx = attempts
    if idx >= len(BACKOFF_SCHEDULE):
        return None
    delay = BACKOFF_SCHEDULE[idx]
    next_dt = datetime.now(timezone.utc) + timedelta(seconds=delay)
    return next_dt.strftime(_TIMESTAMP_FMT)


class OutboxWorker:
    def __init__(self, db_path=None, on_change=None):
        self.db_path = db_path or DB_PATH
        self.on_change = on_change or (lambda: event_bus.publish("refresh"))
        self._stop = threading.Event()
        self._thread = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self):
        return self._running

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="outbox-worker")
            self._thread.start()
            self._running = True
            logger.info("OutboxWorker started")

    def stop(self):
        with self._lock:
            self._running = False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("OutboxWorker stopped")

    def _run(self):
        while not self._stop.is_set():
            try:
                if cache.has_email_credentials(self.db_path):
                    self._process_batch()
            except Exception:
                logger.warning("OutboxWorker error", exc_info=True)
            self._stop.wait(POLL_INTERVAL)
        self._running = False

    def _process_batch(self):
        cache.reclaim_stale_sending(self.db_path)
        due = cache.get_due(self.db_path)
        if not due:
            return
        for row in due:
            if self._stop.is_set():
                break
            self._attempt(row)

    def _attempt(self, row):
        outbox_id = row["id"]
        if not cache.mark_outbox_attempting(outbox_id, self.db_path):
            return
        try:
            email_reader.send_message(
                _split_addrs(row["to_addr"]),
                _split_addrs(row["cc"]),
                _split_addrs(row["bcc"]),
                row["subject"] or "",
                row["body"] or "",
                row["body_html"] or "",
                row["mode"] or "new",
                original_message_id=row["original_message_id"],
                thread_id=row["thread_id"],
                db_path=self.db_path,
            )
        except Exception as exc:
            attempts = int(row["attempts"] or 0) + 1
            next_at = None if attempts >= MAX_ATTEMPTS else _next_attempt_at(int(row["attempts"] or 0))
            cache.mark_outbox_failed(outbox_id, str(exc), next_at, self.db_path)
            if next_at is None:
                logger.warning(
                    "Outbox message %s permanently failed after %d attempts: %s",
                    outbox_id,
                    attempts,
                    exc,
                )
            else:
                logger.info("Outbox message %s attempt %d failed; will retry", outbox_id, attempts)
            try:
                self.on_change()
            except Exception:
                logger.debug("on_change callback error", exc_info=True)
            return

        cache.delete_outbox(outbox_id, self.db_path)
        logger.info("Outbox message %s sent after %d attempt(s)", outbox_id, int(row["attempts"] or 0) + 1)
        try:
            self.on_change()
        except Exception:
            logger.debug("on_change callback error", exc_info=True)

def _split_addrs(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
