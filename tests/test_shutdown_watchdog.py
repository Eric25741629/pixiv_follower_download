"""Tests for the shutdown watchdog (2026-06-21 closeability hardening).

A wedged worker or a post-Flet-session-GC dead window must never leave an
unkillable backend. The watchdog os._exit(0)s after a hard deadline regardless
of session/event-loop/worker state.
"""
import threading
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.gui.flet_app as fa


def test_watchdog_fires_after_timeout(monkeypatch):
    fa._SHUTDOWN_WATCHDOG_ARMED = False
    called = []
    fired = threading.Event()

    def fake_exit(code):
        called.append(code)
        fired.set()

    monkeypatch.setattr(fa.os, "_exit", fake_exit)
    fa._arm_shutdown_watchdog(timeout=0.05, reason="unit-test")
    assert fired.wait(2.0), "watchdog never fired"
    assert called == [0]
    fa._SHUTDOWN_WATCHDOG_ARMED = False


def test_watchdog_arms_only_once(monkeypatch):
    fa._SHUTDOWN_WATCHDOG_ARMED = False
    called = []

    def fake_exit(code):
        called.append(code)

    monkeypatch.setattr(fa.os, "_exit", fake_exit)
    fa._arm_shutdown_watchdog(timeout=0.05, reason="first")
    fa._arm_shutdown_watchdog(timeout=0.05, reason="second")  # no-op
    threading.Event().wait(0.3)
    assert len(called) == 1, f"re-arm started a second watchdog thread: {called}"
    fa._SHUTDOWN_WATCHDOG_ARMED = False


# ── signal-handler isolation (review finding #3) ───────────────────────────

def test_install_crash_hooks_does_not_install_signal_handlers():
    """_install_crash_hooks is called by unit tests; it must NOT leak a
    process-global SIGINT handler (which would turn a later interactive Ctrl+C
    into an abrupt os._exit during pytest)."""
    import signal as _signal
    import sys as _sys
    import threading as _threading

    saved_sys = _sys.excepthook
    saved_thr = getattr(_threading, "excepthook", None)
    saved_sigint = _signal.getsignal(_signal.SIGINT)
    saved_flag = fa._HOOKS_INSTALLED
    try:
        fa._HOOKS_INSTALLED = False
        fa._install_crash_hooks()
        assert _signal.getsignal(_signal.SIGINT) is saved_sigint
    finally:
        _sys.excepthook = saved_sys
        if saved_thr is not None:
            _threading.excepthook = saved_thr
        try:
            _signal.signal(_signal.SIGINT, saved_sigint)
        except Exception:
            pass
        fa._HOOKS_INSTALLED = saved_flag


def test_install_signal_handlers_installs_sigint():
    import signal as _signal

    saved_sigint = _signal.getsignal(_signal.SIGINT)
    saved_flag = fa._SIGNALS_INSTALLED
    try:
        fa._SIGNALS_INSTALLED = False
        fa._install_signal_handlers()
        assert _signal.getsignal(_signal.SIGINT) is not saved_sigint
    finally:
        try:
            _signal.signal(_signal.SIGINT, saved_sigint)
        except Exception:
            pass
        fa._SIGNALS_INSTALLED = saved_flag
