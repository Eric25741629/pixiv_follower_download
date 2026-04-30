from pathlib import Path
import sys
import queue
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.worker_event import WorkerEvent
from app.gui.dispatcher import EventDispatcher


class _FakePage:
    def __init__(self):
        self.update_count = 0
    def update(self):
        self.update_count += 1


def test_dispatcher_routes_output_event():
    page = _FakePage()
    q: queue.Queue = queue.Queue()
    received = []
    handlers = {"output": lambda d: received.append(d)}
    disp = EventDispatcher(page, q, handlers)

    q.put(WorkerEvent("output", "hello"))
    disp._poll_once()

    assert received == ["hello"]
    assert page.update_count == 1


def test_dispatcher_ignores_unknown_event_type():
    page = _FakePage()
    q: queue.Queue = queue.Queue()
    handlers = {}
    disp = EventDispatcher(page, q, handlers)
    q.put(WorkerEvent("unknown_type", None))
    disp._poll_once()
    assert page.update_count == 1


def test_dispatcher_batches_multiple_events():
    page = _FakePage()
    q: queue.Queue = queue.Queue()
    received = []
    handlers = {"output": lambda d: received.append(d)}
    disp = EventDispatcher(page, q, handlers)
    for i in range(5):
        q.put(WorkerEvent("output", str(i)))
    disp._poll_once()
    assert received == ["0", "1", "2", "3", "4"]
    assert page.update_count == 1
