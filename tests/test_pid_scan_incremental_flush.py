"""Tests for Step 2's incremental flush — pictures_id.txt + author_progress.json
must be written periodically during the artist scan, not only at the end, so a
crash mid-run loses less than _STEP2_INCREMENTAL_EVERY artists of work.
"""
from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_pid_scan import get_pixiv_author_imgID_Thread


def _make_thread(tmp_path):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.path = str(tmp_path)
    t._q = Queue()
    t._stop_event = threading.Event()
    t._pause_event = threading.Event()
    t._pause_event.set()
    t.exist_pid = set()
    t._metadata_db = None
    t._init_step2_run_state()
    return t


def test_flush_step2_incremental_writes_pictures_id(tmp_path):
    t = _make_thread(tmp_path)
    t._collected_pids = ["111", "222", "333"]
    t._progress_updates = [("artist_a", "2026-05-18T00:00:00")]

    t._flush_step2_incremental(reason="test")

    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    assert set(pics) == {"111", "222", "333"}
    prog = (tmp_path / "author_progress.json").read_text(encoding="utf-8")
    assert "artist_a" in prog


def test_flush_step2_incremental_merges_with_existing(tmp_path):
    (tmp_path / "pictures_id.txt").write_text("999\n", encoding="utf-8")
    t = _make_thread(tmp_path)
    t._collected_pids = ["111", "222"]

    t._flush_step2_incremental(reason="test")

    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    assert set(pics) == {"999", "111", "222"}


def test_flush_step2_incremental_idempotent_under_concurrent_callers(tmp_path):
    """Two threads calling at once must not corrupt the file or raise."""
    t = _make_thread(tmp_path)
    t._collected_pids = ["111", "222", "333", "444"]

    errors = []

    def call():
        try:
            t._flush_step2_incremental(reason="concurrent")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=call) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == []
    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    assert set(pics) == {"111", "222", "333", "444"}


def test_flush_step2_incremental_noop_when_run_state_uninit(tmp_path):
    """Called before _init_step2_run_state — must not raise."""
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.path = str(tmp_path)
    t._q = Queue()
    # No _init_step2_run_state — _collected_pids doesn't exist yet
    t._flush_step2_incremental(reason="early")
    # No file should be created
    assert not (tmp_path / "pictures_id.txt").exists()
    assert not (tmp_path / "author_progress.json").exists()


def test_flush_for_shutdown_calls_incremental(tmp_path):
    """The window-close / crash hook path must include the incremental save."""
    t = _make_thread(tmp_path)
    t._collected_pids = ["555"]

    t.flush_for_shutdown()

    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    assert "555" in pics
