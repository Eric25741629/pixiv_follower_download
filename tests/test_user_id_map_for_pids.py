"""Tests for MetadataDB.user_id_map_for_pids — bulk pid -> user_id lookup."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB


def _open(tmp_path):
    return MetadataDB(str(tmp_path), event_log=None)


def test_returns_user_id_for_known_pids(tmp_path):
    db = _open(tmp_path)
    db.upsert_artwork("100", user_id="111")
    db.upsert_artwork("200", user_id="222")
    out = db.user_id_map_for_pids(["100", "200"])
    assert out == {"100": "111", "200": "222"}


def test_missing_pid_maps_to_none(tmp_path):
    db = _open(tmp_path)
    db.upsert_artwork("100", user_id="111")
    out = db.user_id_map_for_pids(["100", "999"])
    assert out["100"] == "111"
    assert out["999"] is None


def test_null_or_empty_user_id_maps_to_none(tmp_path):
    db = _open(tmp_path)
    db.upsert_artwork("100")              # no user_id -> NULL
    db.upsert_artwork("200", user_id="")  # empty string
    out = db.user_id_map_for_pids(["100", "200"])
    assert out["100"] is None
    assert out["200"] is None


def test_keys_are_original_input_pids(tmp_path):
    db = _open(tmp_path)
    db.upsert_artwork("100", user_id="111")
    # page-suffixed input coerces to "100" for the query but the returned
    # key must be the exact value passed in.
    out = db.user_id_map_for_pids(["100_p3"])
    assert out == {"100_p3": "111"}


def test_chunking_over_900_pids(tmp_path):
    db = _open(tmp_path)
    pids = [str(i) for i in range(1, 1001)]      # 1000 pids
    for p in pids:
        db.upsert_artwork(p, user_id="u" + p)
    out = db.user_id_map_for_pids(pids)
    assert len(out) == 1000
    assert out["1"] == "u1"
    assert out["1000"] == "u1000"


def test_empty_input_returns_empty(tmp_path):
    db = _open(tmp_path)
    assert db.user_id_map_for_pids([]) == {}
