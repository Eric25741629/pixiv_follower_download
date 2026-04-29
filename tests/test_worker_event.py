from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.worker_event import WorkerEvent


def test_worker_event_is_frozen():
    ev = WorkerEvent("output", "hello")
    try:
        ev.type = "other"
        assert False, "should be immutable"
    except Exception:
        pass


def test_worker_event_fields():
    ev = WorkerEvent("progress", (10, 100))
    assert ev.type == "progress"
    assert ev.data == (10, 100)


def test_worker_event_equality():
    assert WorkerEvent("finished", "done") == WorkerEvent("finished", "done")
    assert WorkerEvent("next", 2) != WorkerEvent("next", 3)
