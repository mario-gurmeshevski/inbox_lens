import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def publish(self, event_type: str, data: dict | None = None):
        msg = {"type": event_type, "data": data or {}}
        with self._lock:
            subscribers = list(self._subscribers)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        for q in subscribers:
            try:
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(q.put_nowait, msg)
                else:
                    q.put_nowait(msg)
            except Exception:
                logger.warning("Failed to publish event to subscriber", exc_info=True)


bus = EventBus()
