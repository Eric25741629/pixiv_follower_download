from __future__ import annotations
import asyncio
import queue
import threading
from typing import Any, Callable

from app.core.worker_event import WorkerEvent


class EventDispatcher:
    """Polls a WorkerEvent queue and dispatches events to Flet UI handlers.

    Must run as an async coroutine via page.run_task(dispatcher.run) so the
    handlers (and therefore control.update() / page.update()) execute on the
    asyncio event-loop thread. Running it via page.run_thread() instead lets
    update() enqueue patches via asyncio.Queue.put_nowait from the wrong
    thread — the consumer task isn't woken, so the UI only repaints when
    *any* user event (drag, click) finally pumps the loop.
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
                    try:
                        handler(ev.data)
                    except Exception:
                        pass
                updated = True
        except queue.Empty:
            pass
        if updated:
            try:
                self._page.update()
            except Exception:
                pass

    async def run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        self._stop_event.set()
