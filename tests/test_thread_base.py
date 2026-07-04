from pathlib import Path
import sys
import queue
import time
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.worker_event import WorkerEvent
from app.core.pixiv_thread_base import PauseableThread


class _NoopThread(PauseableThread):
    def run(self):
        self._sleep_with_countdown(2)


def test_pause_emits_event():
    q: queue.Queue = queue.Queue()
    t = _NoopThread(q)
    t.pause()
    ev = q.get_nowait()
    assert ev.type == "output"
    assert "已暫停" in ev.data


def test_resume_emits_event():
    q: queue.Queue = queue.Queue()
    t = _NoopThread(q)
    t.pause()
    q.get_nowait()  # discard pause event
    t.resume()
    ev = q.get_nowait()
    assert ev.type == "output"
    assert "已繼續" in ev.data


def test_stop_sets_stop_event_and_emits():
    q: queue.Queue = queue.Queue()
    t = _NoopThread(q)
    t.stop()
    assert t._stop_event.is_set()
    ev = q.get_nowait()
    assert ev.type == "output"
    assert "已停止" in ev.data


def test_countdown_emits_countdown_events():
    q: queue.Queue = queue.Queue()
    t = _NoopThread(q)
    t.start()
    time.sleep(2.5)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    countdown_events = [e for e in events if e.type == "countdown"]
    values = [e.data for e in countdown_events]
    assert 2 in values
    assert 1 in values
    assert 0 in values


# --- new tests for scheduler injection (Task 4) ---

def test_pauseable_thread_default_scheduler_is_none():
    import queue
    from app.core.pixiv_thread_base import PauseableThread
    t = PauseableThread(queue.Queue())
    assert t._scheduler is None


def test_acquire_account_returns_none_when_no_scheduler():
    import queue
    from app.core.pixiv_thread_base import PauseableThread
    t = PauseableThread(queue.Queue())
    assert t._acquire_account() is None


def test_release_account_is_noop_when_no_scheduler():
    import queue
    from app.core.pixiv_thread_base import PauseableThread
    t = PauseableThread(queue.Queue())
    # Should not raise
    t._release_account(None)
    t._release_account(object(), ok=True)
    t._release_account(object(), ok=False)


def test_acquire_account_delegates_to_scheduler():
    import queue
    from app.core.pixiv_thread_base import PauseableThread

    class FakeScheduler:
        def __init__(self, value):
            self.value = value
            self.acquire_called = 0
        def acquire(self):
            self.acquire_called += 1
            return self.value

    sentinel = object()
    sched = FakeScheduler(sentinel)
    t = PauseableThread(queue.Queue(), scheduler=sched)
    result = t._acquire_account()
    assert result is sentinel
    assert sched.acquire_called == 1


def test_release_account_delegates_to_scheduler():
    import queue
    from app.core.pixiv_thread_base import PauseableThread

    class FakeScheduler:
        def __init__(self):
            self.release_args = None
        def release(self, account, ok=True, work_units=1):
            self.release_args = (account, ok)

    sched = FakeScheduler()
    t = PauseableThread(queue.Queue(), scheduler=sched)
    acc = object()
    t._release_account(acc, ok=False)
    assert sched.release_args == (acc, False)


def test_release_account_skips_when_account_is_none():
    import queue
    from app.core.pixiv_thread_base import PauseableThread

    class FakeScheduler:
        def __init__(self):
            self.release_called = False
        def release(self, account, ok=True, work_units=1):
            self.release_called = True

    sched = FakeScheduler()
    t = PauseableThread(queue.Queue(), scheduler=sched)
    t._release_account(None)
    assert sched.release_called is False
