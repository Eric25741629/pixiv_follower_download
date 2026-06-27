"""MetadataDB.backfill_user_ids: UPDATE-only author backfill for existing
artworks rows (never inserts, so it cannot disturb any work queue), plus its
event-log replay handler."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.core.metadata_db import MetadataDB
from app.core.event_log import _dispatch_table


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


class _FakeLog:
    def __init__(self):
        self.events = []

    def emit(self, kind, **fields):
        self.events.append((kind, fields))


def test_backfill_sets_null_user_id(tmp_path):
    db = MetadataDB(str(tmp_path), event_log=None)
    db.upsert_artworks(["10", "20"])  # NULL user_id
    n = db.backfill_user_ids(["10", "20"], "A")
    assert n == 2
    assert db.user_id_map_for_pids(["10", "20"]) == {"10": "A", "20": "A"}


def test_backfill_does_not_overwrite_existing_author(tmp_path):
    db = MetadataDB(str(tmp_path), event_log=None)
    db.upsert_artworks(["30"], user_id="B")
    db.backfill_user_ids(["30"], "A")  # first-writer-wins
    assert db.user_id_map_for_pids(["30"])["30"] == "B"


def test_backfill_never_inserts_unknown_pid(tmp_path):
    db = MetadataDB(str(tmp_path), event_log=None)
    db.backfill_user_ids(["99"], "A")  # 99 not in artworks
    assert db.get_artwork("99") is None


def test_backfill_empty_user_id_is_noop(tmp_path):
    db = MetadataDB(str(tmp_path), event_log=None)
    db.upsert_artworks(["10"])
    assert db.backfill_user_ids(["10"], "") == 0
    assert db.backfill_user_ids(["10"], None) == 0
    assert db.user_id_map_for_pids(["10"])["10"] is None


def test_backfill_emits_replay_event(tmp_path):
    log = _FakeLog()
    db = MetadataDB(str(tmp_path), event_log=log)
    db.upsert_artworks(["10"])
    db.backfill_user_ids(["10"], "A")
    kinds = [k for k, _ in log.events]
    assert "artwork.user_id_backfill" in kinds


def test_replay_handler_registered_and_applies(tmp_path):
    table = _dispatch_table()
    assert "artwork.user_id_backfill" in table
    db = MetadataDB(str(tmp_path), event_log=None)
    db.upsert_artworks(["10"])
    table["artwork.user_id_backfill"](db, {"pids": ["10"], "user_id": "A"})
    assert db.user_id_map_for_pids(["10"])["10"] == "A"
