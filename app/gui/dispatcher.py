from __future__ import annotations
import queue
import threading
from typing import Any, Callable

from app.core.worker_event import WorkerEvent


class EventDispatcher:
    """Polls a WorkerEvent queue and dispatches events to Flet UI handlers.

    Designed to run in a background thread via page.run_thread(dispatcher.run).
    Uses an Event for stop so close-time wake-up is instant rather than
    waiting for the current 50 ms tick to expire.
    """

    def __init__(self, page: Any, q: queue.Queue, handlers: dict[str, Callable]):
        self._page = page
        self._q = q
        self._handlers = handlers
        self._stop_event = threading.Event()

    def _poll_once(self) -> None:
        updated = False
        try:
            while True:
                ev: WorkerEvent = self._q.get_nowait()
                handler = self._handlers.get(ev.type)
                if handler is not None:
                    handler(ev.data)
                updated = True
        except queue.Empty:
            pass
        if updated:
            try:
                self._page.update()
            except Exception:
                pass

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            # wait() returns True when stop is set -> exit immediately
            if self._stop_event.wait(timeout=0.05):
                break

    def stop(self) -> None:
        self._stop_event.set()
