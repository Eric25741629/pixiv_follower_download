"""Step 2 backfills user_id for an artist's FULL PID list (including PIDs the
incremental scan truncated), so author-grouping works for large pre-existing
pending sets without re-querying each PID. Gated behind author_order."""
import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.core.metadata_db import MetadataDB
from app.core.thread_pid_scan import get_pixiv_author_imgID_Thread


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


class _FakeQ:
    def __init__(self):
        self.events = []

    def put(self, ev):
        self.events.append(ev)


def _make(tmp_path, author_order, db=True):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.author_order = author_order
    t._metadata_db = MetadataDB(str(tmp_path), event_log=None) if db else None
    t._step2_db_write_lock = threading.Lock()
    t._q = _FakeQ()
    return t


def test_backfill_full_list_including_skipped(tmp_path):
    t = _make(tmp_path, True)
    db = t._metadata_db
    # 50/40 newer (kept); 30/20 older (would be truncated) — all already in
    # artworks with NULL author (simulating a large pre-existing pending set).
    db.upsert_artworks(["50", "40", "30", "20"])
    t._step2_backfill_author_user_ids(["50", "40", "30", "20"], "123")
    assert db.user_id_map_for_pids(["50", "40", "30", "20"]) == {
        "50": "123", "40": "123", "30": "123", "20": "123",
    }


def test_backfill_off_is_noop(tmp_path):
    t = _make(tmp_path, False)
    db = t._metadata_db
    db.upsert_artworks(["10"])
    t._step2_backfill_author_user_ids(["10"], "123")
    assert db.user_id_map_for_pids(["10"])["10"] is None


def test_backfill_db_none_no_crash(tmp_path):
    t = _make(tmp_path, True, db=False)
    t._step2_backfill_author_user_ids(["10"], "123")  # must not raise


def test_scan_backfills_kept_and_skipped(tmp_path, monkeypatch):
    t = _make(tmp_path, True, db=False)  # capture call, skip real DB
    t.exist_pid = {"30"}  # 30 is the truncation boundary
    t._stats_collector = None
    t.cookie_pool = []
    t._cookie_alias_map = {}
    t._stop_event = threading.Event()
    t._pause_event = threading.Event()
    t._pause_event.set()
    t.path = str(tmp_path)
    t._init_step2_run_state()
    t._step2_early_skip_pids = set()
    t._step2_skip_lock = threading.Lock()

    captured = []
    t._step2_backfill_author_user_ids = lambda pids, author: captured.append((list(pids), author))
    monkeypatch.setattr(t, "_step2_fetch_artist_pid_list",
                        lambda *a, **k: ["50", "40", "30", "20"])

    out = t.thread_no_use_seleium_get_pid("c", "UA", str(tmp_path), "1", "123")
    assert out == ["50", "40"]  # kept (newer than boundary)
    assert captured == [(["50", "40", "30", "20"], "123")]  # full = kept + skipped
