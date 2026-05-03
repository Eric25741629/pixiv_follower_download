"""Tests for the helpers extracted from get_img_url_thread.check_exist."""
from pathlib import Path
import sys
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_url_fetch import get_img_url_thread


def _stub(tmp_path):
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.path = str(tmp_path)
    t.no_to_check = []
    t._q = Queue()
    return t


# ── _load_check_exist_block_set ──────────────────────────────────────────────

def test_block_set_from_no_to_check_list(tmp_path):
    t = _stub(tmp_path)
    t.no_to_check = ["100", "200_p1", "300"]
    s = t._load_check_exist_block_set()
    assert "100" in s
    assert "300" in s


def test_block_set_empty_when_no_to_check_is_not_list(tmp_path):
    t = _stub(tmp_path)
    t.no_to_check = "not a list"
    s = t._load_check_exist_block_set()
    assert s == set()


# ── _load_step2_skip_set ─────────────────────────────────────────────────────

def test_step2_skip_set_loads_lines(tmp_path):
    t = _stub(tmp_path)
    (tmp_path / "step2_skip_pid.txt").write_text("400\n500\n\n", encoding="utf-8")
    s = t._load_step2_skip_set()
    assert "400" in s
    assert "500" in s


def test_step2_skip_set_returns_empty_when_file_missing(tmp_path):
    t = _stub(tmp_path)
    s = t._load_step2_skip_set()
    assert s == set()


# ── _scan_pictures_id_file ───────────────────────────────────────────────────

def test_scan_pictures_id_file_filters_block_set(tmp_path):
    t = _stub(tmp_path)
    pic = tmp_path / "pictures_id.txt"
    pic.write_text("100\n200\n300\n", encoding="utf-8")
    pids, raw, blocked, step2_blocked = t._scan_pictures_id_file(
        str(pic), block_set={"200"}, step2_skip_set=set()
    )
    assert pids == ["100", "300"]
    assert raw == 3
    assert blocked == 1
    assert step2_blocked == 0


def test_scan_pictures_id_file_counts_step2_overlap(tmp_path):
    t = _stub(tmp_path)
    pic = tmp_path / "pictures_id.txt"
    pic.write_text("100\n200\n300\n", encoding="utf-8")
    pids, raw, blocked, step2_blocked = t._scan_pictures_id_file(
        str(pic), block_set={"100", "200"}, step2_skip_set={"100"}
    )
    # 100 is blocked AND in step2_skip -> step2_blocked = 1
    # 200 is blocked but NOT in step2_skip -> step2_blocked stays 1
    assert pids == ["300"]
    assert blocked == 2
    assert step2_blocked == 1


def test_scan_pictures_id_file_skips_blank_lines(tmp_path):
    t = _stub(tmp_path)
    pic = tmp_path / "pictures_id.txt"
    pic.write_text("100\n\n   \n200\n", encoding="utf-8")
    pids, raw, _, _ = t._scan_pictures_id_file(
        str(pic), block_set=set(), step2_skip_set=set()
    )
    assert pids == ["100", "200"]
    assert raw == 2


def test_scan_pictures_id_file_handles_bad_utf8_bytes(tmp_path):
    t = _stub(tmp_path)
    pic = tmp_path / "pictures_id.txt"
    # Write bytes that include an invalid UTF-8 sequence
    pic.write_bytes(b"100\n\xff\xfe\n200\n")
    pids, raw, _, _ = t._scan_pictures_id_file(
        str(pic), block_set=set(), step2_skip_set=set()
    )
    # Both valid lines preserved; the bad bytes are ignored, not crashed
    assert "100" in pids
    assert "200" in pids


# ── _check_exist_candidate_paths ─────────────────────────────────────────────

def test_candidate_paths_includes_self_path(tmp_path):
    t = _stub(tmp_path)
    candidates = t._check_exist_candidate_paths()
    assert any(str(tmp_path) in p for p in candidates)
