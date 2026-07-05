"""Combined-mode finalize must wait for the background JXL backlog.

Combined never runs Step 4's ``_emit_step4_summary_and_finalize`` (the only
other ``_drain_jxl_queue`` call site), so ``combined_thread._finalize`` has to
drain the JXL worker pool itself — otherwise a stop (or normal completion)
releases the UI while conversions are still running in daemon threads.
"""
from pathlib import Path
import sys
import threading
import types
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_combined import combined_thread


def _make_thread(downloader):
    t = combined_thread.__new__(combined_thread)
    t._q = Queue()
    t._emit = lambda html: None
    t.fetcher = types.SimpleNamespace(
        _flush_url_meta_snapshot=lambda full=True: None,
        _persist_pending_pid_file=lambda: None,
        _flush_revoked_pid_file=lambda: None,
    )
    t.downloader = downloader
    return t


def test_finalize_drains_jxl_backlog():
    calls = []
    downloader = types.SimpleNamespace(
        jxl_enable=True,
        _jxl_worker_threads=[threading.current_thread()],  # "alive" worker
        _drain_jxl_queue=lambda: calls.append("drain"),
        _classify_download_results=lambda nested: [],
    )
    _make_thread(downloader)._finalize([])
    assert calls == ["drain"]


def test_finalize_skips_drain_when_jxl_disabled():
    calls = []
    downloader = types.SimpleNamespace(
        jxl_enable=False,
        _jxl_worker_threads=[],
        _drain_jxl_queue=lambda: calls.append("drain"),
        _classify_download_results=lambda nested: [],
    )
    _make_thread(downloader)._finalize([])
    assert calls == []
