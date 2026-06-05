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
