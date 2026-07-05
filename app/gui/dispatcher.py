from __future__ import annotations
import asyncio
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from app.core.app_logging import get_logger
from app.core import diag_log
from app.core.worker_event import WorkerEvent

_log = get_logger("pixiv.dispatcher")

# Events that must repaint immediately even when the window is backgrounded —
# they carry state the user acts on (spinner up/down, run finished, next step)
# rather than high-frequency progress noise.
_DEFAULT_URGENT_TYPES = frozenset({"loading", "finished", "next"})


class EventDispatcher:
    """Polls a WorkerEvent queue and dispatches events to Flet UI handlers.

    Must run as an async coroutine via page.run_task(dispatcher.run) so the
    handlers (and therefore control.update() / page.update()) execute on the
    asyncio event-loop thread. Running it via page.run_thread() instead lets
    update() enqueue patches via asyncio.Queue.put_nowait from the wrong
    thread — the consumer task isn't woken, so the UI only repaints when
    *any* user event (drag, click) finally pumps the loop.

    Background throttle: the queue is still drained every 50 ms (events are
    never dropped or delayed in handling), but when ``is_foreground()`` is
    False the flushing page.update() is coalesced to at most once per
    ``bg_update_interval_sec`` — so a backgrounded window repaints ~1×/s
    instead of ~20×/s. Urgent event types flush immediately regardless, and
    returning to foreground flushes any pending update on the next poll
    (≤50 ms latency).
    """

    def __init__(
        self,
        page: Any,
        q: queue.Queue,
        handlers: dict[str, Callable],
        *,
        is_foreground: Callable[[], bool] | None = None,
        bg_update_interval_sec: float = 1.0,
        urgent_types=_DEFAULT_URGENT_TYPES,
        now: Callable[[], float] = time.monotonic,
    ):
        self._page = page
        self._q = q
        self._handlers = handlers
        self._stop_event = threading.Event()
        # Default (no callback) = always foreground → byte-identical old cadence.
        self._is_foreground = is_foreground if is_foreground is not None else (lambda: True)
        self._bg_interval = max(0.0, float(bg_update_interval_sec))
        self._urgent_types = frozenset(urgent_types)
        self._now = now
        self._dirty = False       # handled events not yet flushed to the client
        self._last_flush = 0.0     # monotonic time of the last page.update()

    def _should_flush(self, urgent: bool) -> bool:
        if urgent:
            return True
        if self._is_foreground():
            return True
        return (self._now() - self._last_flush) >= self._bg_interval

    def _flush(self) -> None:
        try:
            self._page.update()
            self._dirty = False
            self._last_flush = self._now()
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

    def _poll_once(self) -> None:
        urgent = False
        try:
            while True:
                ev: WorkerEvent = self._q.get_nowait()
                # UI-communication trace (opt-in): record every event before it
                # touches the UI so ui_events.log can be cross-referenced with
                # worker.log / download.log. Guarded so the per-event f-string +
                # summary() (an HTML-strip regex on output events) is skipped
                # entirely on the hot path unless diagnostics.verbose_logs is on.
                if diag_log.ui_trace_enabled():
                    diag_log.log(diag_log.UI, f"{ev.type}: {diag_log.summary(ev.data)}")
                handler = self._handlers.get(ev.type)
                if handler is not None:
                    try:
                        handler(ev.data)
                    except Exception:
                        _log.exception("handler %r raised", ev.type)
                self._dirty = True
                if ev.type in self._urgent_types:
                    urgent = True
        except queue.Empty:
            pass
        # _dirty persists across polls, so a backgrounded update deferred this
        # tick still flushes on a later tick (or immediately on refocus) even
        # if no new events arrive.
        if self._dirty and self._should_flush(urgent):
            self._flush()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        self._stop_event.set()
