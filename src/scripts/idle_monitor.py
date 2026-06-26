import imaplib
import logging
import select
import threading
import time

from src.scripts import cache, email_reader
from src.scripts.constants import DB_PATH

logger = logging.getLogger(__name__)


class ConnectionLost(Exception):
    pass


RECONNECT_DELAY = 30
IDLE_CHECK_INTERVAL = 30
IDLE_RENEW_INTERVAL = 25 * 60
POLL_FALLBACK_INTERVAL = 30 * 60


class IdleMonitor:
    def __init__(self, db_path=None, on_refresh=None):
        self.db_path = db_path or DB_PATH
        self.on_refresh = on_refresh
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
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self._running = True
            logger.info("IdleMonitor started")

    def stop(self):
        self._stop.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("IdleMonitor stopped")

    def _run(self):
        while not self._stop.is_set():
            if not cache.has_email_credentials(self.db_path):
                self._stop.wait(RECONNECT_DELAY)
                continue

            try:
                self._idle_loop()
            except Exception:
                logger.warning("IDLE monitor error, reconnecting...", exc_info=True)

            if not self._stop.is_set():
                self._stop.wait(RECONNECT_DELAY)

        self._running = False

    def _check_idle_support(self, conn):
        try:
            status, caps = conn.capability()
            if status == "OK":
                cap_str = b" ".join(caps).decode(errors="replace").upper()
                return "IDLE" in cap_str
        except Exception:
            logger.warning("Failed to check IDLE capability", exc_info=True)
        return False

    def _idle_loop(self):
        conn = None
        try:
            conn = email_reader._imap_connect(self.db_path)

            if not self._check_idle_support(conn):
                logger.error("IMAP server does not support IDLE extension")
                return

            while not self._stop.is_set():
                try:
                    new_mail = self._do_idle(conn)
                except ConnectionLost:
                    logger.warning("Connection lost, reconnecting...")
                    email_reader._safe_close(conn)
                    conn = None
                    self._stop.wait(RECONNECT_DELAY)
                    if self._stop.is_set():
                        break
                    conn = email_reader._imap_connect(self.db_path)
                    continue

                if new_mail:
                    logger.info("New mail detected via IDLE, fetching...")
                    email_reader._safe_close(conn)
                    conn = None

                    self._fetch_new()

                    conn = email_reader._imap_connect(self.db_path)
        finally:
            if conn:
                email_reader._safe_close(conn)

    def _end_idle(self, conn, tag):
        try:
            conn.send(b"DONE\r\n")
        except Exception:
            return False
        saw_mail = False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                line = conn.readline()
                if not line:
                    break
                decoded = line.decode(errors="replace").upper()
                if "EXISTS" in decoded or "RECENT" in decoded:
                    saw_mail = True
                if tag in line:
                    break
            except Exception:
                break
        return saw_mail

    def _do_idle(self, conn):
        cycle_start = time.monotonic()

        while not self._stop.is_set():
            idle_start = time.monotonic()

            try:
                tag = conn._new_tag()
                conn.send(tag + b" IDLE\r\n")
            except (BrokenPipeError, ConnectionResetError, imaplib.IMAP4.abort, OSError) as exc:
                raise ConnectionLost from exc
            except Exception:
                logger.warning("Failed to send IDLE command", exc_info=True)
                return False

            try:
                continuation = conn.readline()
                if continuation is None or b"+" not in continuation[:4]:
                    logger.warning("IDLE not accepted by server: %s", continuation)
                    return False
            except (BrokenPipeError, ConnectionResetError, imaplib.IMAP4.abort, OSError) as exc:
                raise ConnectionLost from exc
            except Exception:
                logger.warning("Failed to read IDLE continuation", exc_info=True)
                return False

            renew = False
            while not self._stop.is_set():
                elapsed = time.monotonic() - idle_start
                if elapsed >= IDLE_RENEW_INTERVAL:
                    saw_mail = self._end_idle(conn, tag)
                    logger.debug("IDLE renewed after %ds", int(elapsed))
                    if saw_mail:
                        return True
                    renew = True
                    break
                since_cycle = time.monotonic() - cycle_start
                if since_cycle >= POLL_FALLBACK_INTERVAL:
                    self._end_idle(conn, tag)
                    logger.debug("IDLE poll fallback after %ds", int(since_cycle))
                    return True

                try:
                    readable, _, _ = select.select([conn.socket()], [], [], IDLE_CHECK_INTERVAL)
                except (OSError, ValueError):
                    self._end_idle(conn, tag)
                    raise ConnectionLost()

                if not readable:
                    if self._stop.is_set():
                        self._end_idle(conn, tag)
                        return False
                    continue

                try:
                    line = conn.readline()
                    if not line:
                        self._end_idle(conn, tag)
                        return False

                    decoded = line.decode(errors="replace").upper()
                    if "EXISTS" in decoded or "RECENT" in decoded:
                        self._end_idle(conn, tag)
                        return True
                except (BrokenPipeError, ConnectionResetError, imaplib.IMAP4.abort, OSError) as exc:
                    self._end_idle(conn, tag)
                    raise ConnectionLost from exc
                except Exception:
                    self._end_idle(conn, tag)
                    return False

            if renew:
                continue

        return False

    def _fetch_new(self):
        try:
            run_initial_fetch(
                db_path=self.db_path,
                on_refresh=self.on_refresh,
            )
        except Exception:
            logger.exception("Auto-fetch failed")


def run_initial_fetch(db_path=None, on_refresh=None):
    db_path = db_path or DB_PATH
    try:
        result = email_reader.fetch_headers_and_cache(db_path=db_path)
        if "error" in result:
            logger.error("Initial fetch error: %s", result["error"])
            return result

        if on_refresh:
            try:
                on_refresh()
            except Exception:
                logger.warning("on_refresh callback error in initial fetch (after headers)", exc_info=True)

        imap_id_pairs = result.get("imap_id_pairs", [])
        if imap_id_pairs:
            email_reader.fetch_bodies_for_ids(imap_id_pairs, db_path=db_path)

        headers_only_ids = cache.get_headers_only_message_ids(db_path)
        if headers_only_ids:
            email_reader.fetch_bodies_by_message_ids(headers_only_ids, db_path=db_path)

        if on_refresh:
            try:
                on_refresh()
            except Exception:
                logger.warning("on_refresh callback error in initial fetch (after bodies)", exc_info=True)

        emails = cache.read_emails(db_path)
        if emails:
            email_reader.scan_emails(emails, db_path)

        logger.info(
            "Initial fetch complete: %d new, %d existing",
            result.get("new_count", 0),
            result.get("existing_count", 0),
        )

        if on_refresh:
            try:
                on_refresh()
            except Exception:
                logger.warning("on_refresh callback error in initial fetch (after scan)", exc_info=True)

        return result
    except Exception:
        logger.exception("Initial fetch failed")
        return {"error": "Initial fetch failed"}
