import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.cli.headless_view import HeadlessView


def test_headless_view_satisfies_runcontroller_surface():
    v = HeadlessView()
    v.set_running(True)
    v.set_step_state(0, "running")
    v._active_thread = object()
    assert v.running is True
    assert v._active_thread is not None


from queue import Queue
from app.core.worker_event import WorkerEvent
from app.cli import headless_runner


def test_pump_exits_on_finished_for_single_step(monkeypatch):
    q = Queue()

    class _Ctrl:
        def __init__(self):
            self._run_all_mode = False
        def on_next(self, n):
            pass

    # Pre-seed the queue: one log line, then a terminal finished.
    q.put(WorkerEvent("output", "<p>hi</p>"))
    q.put(WorkerEvent("finished", "done"))
    code = headless_runner._pump(q, _Ctrl(), run_all=False)
    assert code == 0


def test_pump_exits_on_terminal_next_minus_one():
    q = Queue()

    class _Ctrl:
        _run_all_mode = True
        def on_next(self, n):
            pass

    q.put(WorkerEvent("next", -1))
    code = headless_runner._pump(q, _Ctrl(), run_all=True)
    assert code == 1  # next=-1 signals failure/terminal
