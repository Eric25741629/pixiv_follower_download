"""combined_thread._resolve_combined_order: author-order reorder of the
combined (query + download-only) work list. Uses __new__ to skip the heavy
constructor and stubs only the attributes the method touches."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.thread_combined import combined_thread


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


def _make(author_order, mapping=None, db=True):
    t = combined_thread.__new__(combined_thread)
    t._q = _FakeQ()
    t.author_order = author_order
    db_obj = _FakeDB(mapping or {}) if db else None
    t.fetcher = SimpleNamespace(_metadata_db=db_obj)
    return t


def test_off_preserves_query_then_download_only():
    t = _make(False)
    out = t._resolve_combined_order(["10", "20"], ["30", "40"])
    assert out == [("10", True), ("20", True), ("30", False), ("40", False)]


def test_on_groups_same_author_contiguous():
    # 10->A, 20->B, 30->A; first-encounter author order A,B; within A pid desc
    t = _make(True, {"10": "A", "20": "B", "30": "A"})
    out = t._resolve_combined_order(["10", "20", "30"], [])
    assert [p for p, _ in out] == ["30", "10", "20"]


def test_on_merges_both_batches_then_groups():
    # query: 10(A) 20(B); download_only: 30(A) 40(B)
    t = _make(True, {"10": "A", "20": "B", "30": "A", "40": "B"})
    out = t._resolve_combined_order(["10", "20"], ["30", "40"])
    assert [p for p, _ in out] == ["30", "10", "40", "20"]
    nq = dict(out)
    assert nq["10"] is True and nq["20"] is True  # query pids keep needs_query
    assert nq["30"] is False and nq["40"] is False  # download-only keep False


def test_on_unknown_author_bucket_last_and_warns():
    t = _make(True, {"10": "A", "20": None})
    out = t._resolve_combined_order(["10", "20"], [])
    assert [p for p, _ in out] == ["10", "20"]  # unknown 20 last
    texts = [str(getattr(ev, "data", "")) for ev in t._q.events]
    assert any("作者不明" in x for x in texts)


def test_on_db_none_falls_back_unchanged():
    t = _make(True, db=False)
    out = t._resolve_combined_order(["10", "20"], ["30"])
    assert out == [("10", True), ("20", True), ("30", False)]


def test_on_dedup_query_wins_over_download_only():
    t = _make(True, {"10": "A"})
    out = t._resolve_combined_order(["10"], ["10"])
    assert out == [("10", True)]
