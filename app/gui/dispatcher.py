from __future__ import annotations
import asyncio
import queue
import threading
from typing import Any, Callable

from app.core.app_logging import get_logger
from app.core.worker_event import WorkerEvent

_log = get_logger("pixiv.dispatcher")


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
                        _log.exception("handler %r raised", ev.type)
                updated = True
        except queue.Empty:
            pass
        if updated:
            try:
                self._page.update()
            except RuntimeError as err:
                # Flet GCs the session if the window is blurred for several
                # minutes (observed: ~6 min). The page's underlying session
                # becomes invalid and every page.update() then raises
                # "An attempt to fetch destroyed session." A fresh main()
                # starts a NEW dispatcher with a NEW page; the old one has
                # nothing useful to do, so self-stop instead of spamming
                # ~20 ERROR lines/second forever.
                if "destroyed session" in str(err):
                    _log.warning(
                        "dispatcher self-stopping: session destroyed "
                        "(new main() will spin up a fresh dispatcher)"
                    )
                    self._stop_event.set()
                else:
                    _log.exception("page.update() in dispatcher failed")
            except Exception:
                _log.exception("page.update() in dispatcher failed")

    async def run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        self._stop_event.set()
