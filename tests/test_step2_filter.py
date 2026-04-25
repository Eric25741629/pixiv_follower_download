"""Tests for get_pixiv_author_imgID_Thread helper methods (Phase 9 refactoring)."""
from pathlib import Path
import sys
import datetime
import json
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_pid_scan import get_pixiv_author_imgID_Thread


class _Signal:
    def emit(self, *_):
        pass


def _stub(author_list=None):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.Author_list = author_list or []
    t._output = _Signal()
    return t


# ── _filter_work_list ────────────────────────────────────────────────────────

def test_filter_work_list_empty_progress():
    """All artists processed when progress is empty."""
    t = _stub(author_list=["A1", "A2", "A3"])
    result = t._filter_work_list({})
    assert result == ["A1", "A2", "A3"]


def test_filter_work_list_recent_artists_skipped():
    """Artists processed within 30 days are excluded."""
    recent = (datetime.datetime.now() - datetime.timedelta(days=5)).isoformat()
    old = (datetime.datetime.now() - datetime.timedelta(days=40)).isoformat()
    progress = {"A1": recent, "A2": old}
    t = _stub(author_list=["A1", "A2", "A3"])
    result = t._filter_work_list(progress)
    assert "A1" not in result    # within 30 days → skip
    assert "A2" in result        # 40 days ago → process
    assert "A3" in result        # no record → process


def test_filter_work_list_exactly_29_days_skipped():
    recent = (datetime.datetime.now() - datetime.timedelta(days=29)).isoformat()
    t = _stub(author_list=["A1"])
    result = t._filter_work_list({"A1": recent})
    assert result == []


def test_filter_work_list_exactly_30_days_processed():
    old = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat()
    t = _stub(author_list=["A1"])
    result = t._filter_work_list({"A1": old})
    assert result == ["A1"]


def test_filter_work_list_invalid_timestamp_treated_as_unprocessed():
    t = _stub(author_list=["A1"])
    result = t._filter_work_list({"A1": "not-a-date"})
    assert result == ["A1"]


def test_filter_work_list_all_empty():
    t = _stub(author_list=[])
    result = t._filter_work_list({})
    assert result == []


# ── _load_author_progress ────────────────────────────────────────────────────

def test_load_author_progress_missing_file(tmp_path):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.path = str(tmp_path)
    t._output = _Signal()
    result = t._load_author_progress()
    assert result == {}


def test_load_author_progress_reads_existing_json(tmp_path):
    data = {"123": "2025-01-01T00:00:00"}
    (tmp_path / "author_progress.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.path = str(tmp_path)
    t._output = _Signal()
    result = t._load_author_progress()
    assert result == data


def test_load_author_progress_corrupt_json_returns_empty(tmp_path):
    (tmp_path / "author_progress.json").write_text("not json", encoding="utf-8")
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.path = str(tmp_path)
    t._output = _Signal()
    result = t._load_author_progress()
    assert result == {}


# ── _commit_step2_outputs ────────────────────────────────────────────────────

def _stub_for_commit(tmp_path, exist_pid=None):
    import threading
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.path = str(tmp_path)
    t._output = _Signal()
    t.exist_pid = set(exist_pid or [])
    t._collected_pids = []
    t._collected_pids_lock = threading.Lock()
    t._progress_updates = []
    t._progress_updates_lock = threading.Lock()
    t._step2_early_skip_pids = set()
    t._step2_skip_lock = threading.Lock()
    return t


def test_commit_step2_outputs_writes_pictures_id(tmp_path):
    t = _stub_for_commit(tmp_path)
    t._commit_step2_outputs(["111", "222", "333"])
    content = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8")
    pids = [l.strip() for l in content.splitlines() if l.strip()]
    assert "111" in pids
    assert "222" in pids
    assert "333" in pids


def test_commit_step2_outputs_skips_existing_pids(tmp_path):
    t = _stub_for_commit(tmp_path, exist_pid=["111"])
    t._commit_step2_outputs(["111", "222"])
    content = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8")
    pids = [l.strip() for l in content.splitlines() if l.strip()]
    assert "111" not in pids  # already in exist_pid
    assert "222" in pids


def test_commit_step2_outputs_no_duplicates(tmp_path):
    # Pre-populate pictures_id.txt with "111"
    (tmp_path / "pictures_id.txt").write_text("111\n", encoding="utf-8")
    t = _stub_for_commit(tmp_path)
    t._commit_step2_outputs(["111", "222"])
    content = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8")
    pids = [l.strip() for l in content.splitlines() if l.strip()]
    assert pids.count("111") == 1  # no duplicate
    assert "222" in pids
