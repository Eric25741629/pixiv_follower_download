"""Tests for download_thread._reorder_pid_order_by_author and
_resolve_execution_order. We use __new__ to skip the heavy __init__ and
stub only the attributes these methods touch."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


class _FakeQ:
    def __init__(self):
        self.events = []

    def put(self, ev):
        self.events.append(ev)


class _FakeDB:
    def __init__(self, mapping):
        self._m = mapping

    def user_id_map_for_pids(self, pids):
        return {p: self._m.get(p) for p in pids}


def _make(mapping):
    t = download_thread.__new__(download_thread)
    t._q = _FakeQ()
    t._metadata_db = _FakeDB(mapping)
    t._emit_phase = lambda *a, **k: None
    return t


def test_reorder_groups_by_author_and_buckets_unknown_last():
    t = _make({"10": "A", "20": "B", "30": "A"})
    flat, batches = t._reorder_pid_order_by_author(["10", "20", "30"])
    assert batches == [["30", "10"], ["20"]]
    assert flat == ["30", "10", "20"]


def test_reorder_emits_unknown_count_warning():
    t = _make({"10": "A", "20": None})
    t._reorder_pid_order_by_author(["10", "20"])
    texts = [str(getattr(ev, "data", "")) for ev in t._q.events]
    assert any("作者不明" in x for x in texts)


def test_reorder_no_warning_when_all_known():
    t = _make({"10": "A", "20": "A"})
    t._reorder_pid_order_by_author(["10", "20"])
    texts = [str(getattr(ev, "data", "")) for ev in t._q.events]
    assert not any("作者不明" in x for x in texts)


def test_reorder_falls_back_to_single_batch_without_db():
    t = download_thread.__new__(download_thread)
    t._metadata_db = None
    flat, batches = t._reorder_pid_order_by_author(["10", "20"])
    assert flat == ["10", "20"]
    assert batches == [["10", "20"]]


def test_resolve_execution_order_off_returns_unchanged_single_batch():
    t = download_thread.__new__(download_thread)
    t.author_order = False
    flat, batches = t._resolve_execution_order(["10", "20", "30"])
    assert flat == ["10", "20", "30"]
    assert batches == [["10", "20", "30"]]


def test_resolve_execution_order_on_delegates_to_reorder():
    t = _make({"10": "A", "20": "B"})
    t.author_order = True
    flat, batches = t._resolve_execution_order(["10", "20"])
    assert batches == [["10"], ["20"]]
