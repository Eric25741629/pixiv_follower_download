import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from queue import Queue
from app.core.worker_event import WorkerEvent
from app.cli import headless_runner


def test_pump_forwards_next_to_controller_on_next():
    q = Queue()
    seen = []

    class _Ctrl:
        _run_all_mode = True
        def on_next(self, n):
            seen.append(n)
            # emit terminal after the chained step so the pump exits
            if n == 2:
                q.put(WorkerEvent("next", -1))

    q.put(WorkerEvent("next", 2))
    headless_runner._pump(q, _Ctrl(), run_all=True)
    assert seen == [2]


def test_normal_run_all_exits_zero_on_step4_finished():
    q = Queue()

    class _Ctrl:
        _run_all_mode = True
        def on_next(self, n):
            pass

    # Simulate reaching step 4, then its terminal bare 'finished'.
    q.put(WorkerEvent("next", 4))
    q.put(WorkerEvent("finished", "下載完成"))
    code = headless_runner._pump(q, _Ctrl(), run_all=True, initial_step=1)
    assert code == 0


def test_combined_run_all_exits_zero_on_finished_then_next_minus_one():
    q = Queue()

    class _Ctrl:
        _run_all_mode = True
        def on_next(self, n):
            pass

    # Combined ends at step 3: finished then terminal next=-1 (success).
    q.put(WorkerEvent("next", 3))
    q.put(WorkerEvent("finished", "邊查邊下完成"))
    q.put(WorkerEvent("next", -1))
    code = headless_runner._pump(q, _Ctrl(), run_all=True, initial_step=1)
    assert code == 0
