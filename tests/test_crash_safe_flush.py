"""Tests for the global crash-safe flush hooks installed in flet_app.py.

The hook must fire flush_for_shutdown() on the active worker thread for any
unexpected exit path (sys.excepthook for main-thread crashes,
threading.excepthook for worker-thread crashes, atexit for other exits).
A regression here would mean an unhandled exception drops in-flight Step 2
PIDs, Step 3 url_meta, or Step 4 download progress to /dev/null.
"""
from __future__ import annotations
import sys
import threading
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.gui import flet_app


class _FakeWorker:
    """Stands in for an active worker; records flush_for_shutdown invocations."""
    def __init__(self):
        self.flush_calls = 0

    def flush_for_shutdown(self):
        self.flush_calls += 1


def _reset_flush_state(monkeypatch, worker):
    """Wire a fresh fake worker + reset the done-flag so emergency_flush runs."""
    monkeypatch.setattr(flet_app, "_PERSISTENT_ACTIVE_THREAD", worker, raising=False)
    monkeypatch.setattr(flet_app, "_EMERGENCY_FLUSH_DONE", False, raising=False)


def test_emergency_flush_calls_active_thread(monkeypatch):
    worker = _FakeWorker()
    _reset_flush_state(monkeypatch, worker)
    flet_app._emergency_flush(reason="unit-test")
    assert worker.flush_calls == 1


def test_emergency_flush_is_idempotent(monkeypatch):
    """Second call must be a no-op so excepthook + atexit don't double-flush."""
    worker = _FakeWorker()
    _reset_flush_state(monkeypatch, worker)
    flet_app._emergency_flush("first")
    flet_app._emergency_flush("second")
    assert worker.flush_calls == 1


def test_emergency_flush_swallows_worker_exceptions(monkeypatch):
    """Must not raise — that would mask the original crash traceback."""
    class _Bad:
        def flush_for_shutdown(self):
            raise RuntimeError("simulated flush failure")
    monkeypatch.setattr(flet_app, "_PERSISTENT_ACTIVE_THREAD", _Bad(), raising=False)
    monkeypatch.setattr(flet_app, "_EMERGENCY_FLUSH_DONE", False, raising=False)
    flet_app._emergency_flush("with-bad-worker")  # must not raise


def test_emergency_flush_handles_no_active_thread(monkeypatch):
    """Resting state (no worker running) must not crash."""
    monkeypatch.setattr(flet_app, "_PERSISTENT_ACTIVE_THREAD", None, raising=False)
    monkeypatch.setattr(flet_app, "_EMERGENCY_FLUSH_DONE", False, raising=False)
    flet_app._emergency_flush("no-worker")


def test_install_crash_hooks_idempotent(monkeypatch):
    """main(page) is called repeatedly after Flet session GC; the hook must
    only install once, otherwise sys.excepthook chains keep growing."""
    monkeypatch.setattr(flet_app, "_HOOKS_INSTALLED", False, raising=False)
    saved_sys = sys.excepthook
    saved_thr = threading.excepthook
    try:
        flet_app._install_crash_hooks()
        h1 = sys.excepthook
        flet_app._install_crash_hooks()  # second call must be a no-op
        h2 = sys.excepthook
        assert h1 is h2
    finally:
        sys.excepthook = saved_sys
        threading.excepthook = saved_thr
        flet_app._HOOKS_INSTALLED = False


def test_sys_excepthook_path_fires_emergency_flush(monkeypatch):
    """Simulate an unhandled exception in the main thread."""
    monkeypatch.setattr(flet_app, "_HOOKS_INSTALLED", False, raising=False)
    saved_sys = sys.excepthook
    saved_thr = threading.excepthook
    try:
        worker = _FakeWorker()
        _reset_flush_state(monkeypatch, worker)
        # Prevent the chained default hook from printing to test stderr.
        monkeypatch.setattr(sys, "excepthook", lambda *a, **k: None)
        flet_app._install_crash_hooks()
        try:
            raise ValueError("simulated main-thread crash")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        assert worker.flush_calls == 1
    finally:
        sys.excepthook = saved_sys
        threading.excepthook = saved_thr
        flet_app._HOOKS_INSTALLED = False


def test_threading_excepthook_path_fires_emergency_flush(monkeypatch):
    """Simulate an unhandled exception inside a worker thread."""
    monkeypatch.setattr(flet_app, "_HOOKS_INSTALLED", False, raising=False)
    saved_sys = sys.excepthook
    saved_thr = threading.excepthook
    try:
        worker = _FakeWorker()
        _reset_flush_state(monkeypatch, worker)
        monkeypatch.setattr(threading, "excepthook", lambda *a, **k: None)
        flet_app._install_crash_hooks()
        args = types.SimpleNamespace(
            exc_type=RuntimeError,
            exc_value=RuntimeError("simulated worker crash"),
            exc_traceback=None,
            thread=threading.current_thread(),
        )
        threading.excepthook(args)
        assert worker.flush_calls == 1
    finally:
        sys.excepthook = saved_sys
        threading.excepthook = saved_thr
        flet_app._HOOKS_INSTALLED = False
