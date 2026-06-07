"""get_pixiv_author_imgID_Thread._regroup_pictures_id_by_author: final
author-grouped rewrite of pictures_id.txt. Uses __new__ + real tmp files."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.thread_pid_scan import get_pixiv_author_imgID_Thread


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


def _make(tmp_path, author_order, mapping=None, db=True):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t._q = _FakeQ()
    t.path = str(tmp_path)
    t.author_order = author_order
    t._metadata_db = _FakeDB(mapping or {}) if db else None
    return t


def _write_pics(tmp_path, pids):
    p = os.path.join(str(tmp_path), "pictures_id.txt")
    with open(p, "w", encoding="utf-8") as f:
        for x in pids:
            f.write(str(x) + "\n")
    return p


def _read_pics(tmp_path):
    p = os.path.join(str(tmp_path), "pictures_id.txt")
    with open(p, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def test_regroup_orders_same_author_contiguous(tmp_path):
    _write_pics(tmp_path, ["10", "20", "30"])  # 10A 20B 30A
    t = _make(tmp_path, True, {"10": "A", "20": "B", "30": "A"})
    t._regroup_pictures_id_by_author()
    assert _read_pics(tmp_path) == ["30", "10", "20"]


def test_regroup_unknown_author_last(tmp_path):
    _write_pics(tmp_path, ["20", "10"])  # 20 unknown, 10 A
    t = _make(tmp_path, True, {"10": "A", "20": None})
    t._regroup_pictures_id_by_author()
    assert _read_pics(tmp_path) == ["10", "20"]


def test_regroup_off_does_not_touch_file(tmp_path):
    _write_pics(tmp_path, ["20", "10"])
    t = _make(tmp_path, False, {"10": "A", "20": "A"})
    t._regroup_pictures_id_by_author()
    assert _read_pics(tmp_path) == ["20", "10"]  # untouched


def test_regroup_db_none_no_crash(tmp_path):
    _write_pics(tmp_path, ["20", "10"])
    t = _make(tmp_path, True, db=False)
    t._regroup_pictures_id_by_author()  # must not raise
    assert _read_pics(tmp_path) == ["20", "10"]  # untouched


def test_regroup_empty_file_no_crash(tmp_path):
    _write_pics(tmp_path, [])
    t = _make(tmp_path, True, {})
    t._regroup_pictures_id_by_author()
    assert _read_pics(tmp_path) == []
