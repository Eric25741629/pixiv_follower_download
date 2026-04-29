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
