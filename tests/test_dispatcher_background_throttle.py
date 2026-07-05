import os, sys, queue
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.gui.dispatcher import EventDispatcher
from app.core.worker_event import WorkerEvent


class FakePage:
    def __init__(self):
        self.updates = 0

    def update(self):
        self.updates += 1


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t


def _mk(page, q, *, is_foreground, clock, bg=1.0):
    return EventDispatcher(
        page, q, {"output": lambda d: None, "loading": lambda d: None},
        is_foreground=is_foreground, bg_update_interval_sec=bg, now=clock.now,
    )


def test_foreground_flushes_every_poll():
    page, q, clock = FakePage(), queue.Queue(), FakeClock()
    d = _mk(page, q, is_foreground=lambda: True, clock=clock)
    q.put(WorkerEvent("output", "a"))
    d._poll_once()
    q.put(WorkerEvent("output", "b"))
    d._poll_once()
    assert page.updates == 2  # foreground = no throttle, one flush per dirty poll


def test_background_coalesces_updates():
    page, q, clock = FakePage(), queue.Queue(), FakeClock()
    d = _mk(page, q, is_foreground=lambda: False, clock=clock, bg=1.0)
    # First background event flushes immediately (last_flush=0, now large enough).
    clock.t = 5.0
    q.put(WorkerEvent("output", "a"))
    d._poll_once()
    assert page.updates == 1
    # Subsequent events within the interval are handled but NOT flushed.
    clock.t = 5.5
    q.put(WorkerEvent("output", "b"))
    d._poll_once()
    q.put(WorkerEvent("output", "c"))
    d._poll_once()
    assert page.updates == 1
    # Once the interval elapses, the pending (dirty) update flushes — even with
    # no new event this poll.
    clock.t = 6.1
    d._poll_once()
    assert page.updates == 2


def test_urgent_event_flushes_in_background():
    page, q, clock = FakePage(), queue.Queue(), FakeClock()
    d = _mk(page, q, is_foreground=lambda: False, clock=clock, bg=1.0)
    clock.t = 5.0
    q.put(WorkerEvent("output", "a"))
    d._poll_once()  # flush 1
    clock.t = 5.2
    q.put(WorkerEvent("output", "b"))  # non-urgent, throttled
    q.put(WorkerEvent("loading", (True, "x")))  # urgent → forces flush
    d._poll_once()
    assert page.updates == 2


def test_refocus_flushes_pending():
    page, q, clock = FakePage(), queue.Queue(), FakeClock()
    fg = {"v": False}
    # Huge bg interval so nothing flushes while backgrounded within the test.
    d = _mk(page, q, is_foreground=lambda: fg["v"], clock=clock, bg=1000.0)
    clock.t = 5.0
    q.put(WorkerEvent("output", "a"))
    d._poll_once()  # throttled (5.0 - 0 < 1000), dirty pending
    clock.t = 5.1
    q.put(WorkerEvent("output", "b"))
    d._poll_once()  # still throttled, dirty pending
    assert page.updates == 0
    # Return to foreground: next poll flushes the pending update immediately.
    fg["v"] = True
    d._poll_once()
    assert page.updates == 1


def test_no_events_no_flush():
    page, q, clock = FakePage(), queue.Queue(), FakeClock()
    d = _mk(page, q, is_foreground=lambda: True, clock=clock)
    d._poll_once()
    assert page.updates == 0


def test_destroyed_session_self_stops():
    class DeadPage:
        def update(self):
            raise RuntimeError("An attempt to fetch destroyed session.")

    q, clock = queue.Queue(), FakeClock()
    d = EventDispatcher(DeadPage(), q, {"output": lambda d: None},
                        is_foreground=lambda: True, now=clock.now)
    q.put(WorkerEvent("output", "a"))
    d._poll_once()
    assert d._stop_event.is_set()


def test_default_construction_is_foreground():
    # No is_foreground/now passed = legacy 3-arg signature still works and
    # behaves as always-foreground (unthrottled).
    page, q = FakePage(), queue.Queue()
    d = EventDispatcher(page, q, {"output": lambda d: None})
    q.put(WorkerEvent("output", "a"))
    d._poll_once()
    q.put(WorkerEvent("output", "b"))
    d._poll_once()
    assert page.updates == 2
