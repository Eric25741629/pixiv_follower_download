from __future__ import annotations
import queue
import time
from typing import Any, Callable

from app.core.worker_event import WorkerEvent


class EventDispatcher:
    """Polls a WorkerEvent queue and dispatches events to Flet UI handlers.

    Designed to run in a background thread via page.run_thread(dispatcher.run).
    Batches all pending events in each 50 ms window into a single page.update().
    """

    def __init__(self, page: Any, q: queue.Queue, handlers: dict[str, Callable]):
        self._page = page
        self._q = q
        self._handlers = handlers
        self._stop = False

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
            self._page.update()

    def run(self) -> None:
        while not self._stop:
            self._poll_once()
            time.sleep(0.05)

    def stop(self) -> None:
        self._stop = True
