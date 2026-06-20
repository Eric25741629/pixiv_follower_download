"""The per-event UI trace (ui_events.log) is opt-in: the dispatcher hot path
must not pay the per-event f-string + summary() regex + file write by default.
worker/download channels stay on (debug-critical, low rate)."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import diag_log


def test_ui_trace_off_by_default():
    assert diag_log.ui_trace_enabled() is False


def test_ui_log_is_noop_when_trace_off(monkeypatch):
    reached = []
    monkeypatch.setattr(diag_log, "_get", lambda ch: reached.append(ch) or None)
    diag_log.configure_ui_trace(False)
    diag_log.log(diag_log.UI, "anything")
    assert reached == []  # returns before _get -> no logger, no file write


def test_worker_channel_unaffected_by_ui_trace(monkeypatch):
    reached = []
    monkeypatch.setattr(diag_log, "_get", lambda ch: reached.append(ch) or None)
    diag_log.configure_ui_trace(False)
    diag_log.log(diag_log.WORKER, "x")
    diag_log.log(diag_log.DOWNLOAD, "y")
    assert reached == [diag_log.WORKER, diag_log.DOWNLOAD]  # always on


def test_ui_trace_on_enables_ui_logging(monkeypatch):
    reached = []
    monkeypatch.setattr(diag_log, "_get", lambda ch: reached.append(ch) or None)
    try:
        diag_log.configure_ui_trace(True)
        assert diag_log.ui_trace_enabled() is True
        diag_log.log(diag_log.UI, "x")
        assert reached == [diag_log.UI]
    finally:
        diag_log.configure_ui_trace(False)  # restore module state
