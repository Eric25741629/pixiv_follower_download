"""Tests for compute_author_order — pure author-grouping reorder."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import compute_author_order


def test_groups_by_author_preserving_first_encounter_order():
    # author B appears first, then A. Output keeps B-before-A.
    pid_order = ["10", "20", "30"]      # 10->B, 20->A, 30->B
    uid = {"10": "B", "20": "A", "30": "B"}
    flat, batches = compute_author_order(pid_order, uid)
    # B's batch first (30,10 desc), then A's (20)
    assert batches == [["30", "10"], ["20"]]
    assert flat == ["30", "10", "20"]


def test_within_author_pid_descending():
    pid_order = ["5", "100", "30"]
    uid = {"5": "A", "100": "A", "30": "A"}
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["100", "30", "5"]]
    assert flat == ["100", "30", "5"]


def test_unknown_authors_bucketed_last():
    pid_order = ["10", "20", "30"]      # 20 unknown
    uid = {"10": "A", "20": None, "30": "A"}
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["30", "10"], ["20"]]   # unknown bucket last
    assert flat == ["30", "10", "20"]


def test_empty_string_user_id_is_unknown():
    pid_order = ["10", "20"]
    uid = {"10": "", "20": "A"}
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["20"], ["10"]]


def test_missing_pid_in_map_is_unknown():
    pid_order = ["10", "20"]
    uid = {"10": "A"}                   # 20 absent
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["10"], ["20"]]


def test_non_numeric_pid_falls_back_to_reverse_lexical_after_digits():
    pid_order = ["abc", "100", "9"]
    uid = {"abc": "A", "100": "A", "9": "A"}
    flat, batches = compute_author_order(pid_order, uid)
    # digits descending first (100, 9), then non-digits reverse-lexical (abc)
    assert batches == [["100", "9", "abc"]]


def test_empty_input():
    flat, batches = compute_author_order([], {})
    assert flat == []
    assert batches == []


def test_only_unknown_authors():
    pid_order = ["30", "10", "20"]
    uid = {}
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["30", "20", "10"]]   # single unknown bucket, PID desc
    assert flat == ["30", "20", "10"]
