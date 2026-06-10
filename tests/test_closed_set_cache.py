"""Tests for the closed-artwork-set fast composition + file-signature cache.

These pin down the optimisation that replaced the ~23s ``v_closed_artworks``
SQL UNION with a Python set composition plus a process-global cache keyed by
the DB file signature. The cache must:
  * return a result identical to recomputing from scratch,
  * serve repeated calls without recomputing while the DB is unchanged,
  * recompute automatically after any write (signature change),
  * hand back an independent (mutable) copy each time.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.core.metadata_db as mdb
from app.core.metadata_db import MetadataDB


_URL = "https://i.pximg.net/img-original/img/2026/01/01/00/00/00/{pid}_p0.png"


def _seed(db):
    """Build one of each closed-kind plus an explicitly open PID."""
    # complete: meta + page_count met by downloaded pages
    db.upsert_meta("10001", page_count=1, like_count=10, img_url="u")
    db.upsert_pending_urls([(_URL.format(pid="10001"), "10001")])
    db.mark_urls_done([_URL.format(pid="10001")])
    # revoked
    db.upsert_meta("20002", page_count=1, img_url="u")
    db.mark_artwork_revoked("20002")
    # legacy sentinel, no pending pages -> closed
    db.import_downloaded_set(["30003"])
    # open: sentinel-free, has a pending page -> NOT closed
    db.upsert_meta("40004", page_count=2, img_url="u")
    db.upsert_pending_urls([(_URL.format(pid="40004"), "40004")])


def test_composition_matches_expected_closed_set(tmp_path):
    db = MetadataDB(str(tmp_path))
    _seed(db)
    closed = db.closed_artwork_set()
    assert closed == {"10001", "20002", "30003"}
    assert "40004" not in closed
    db.close()


def test_downloaded_count_matches_closed_set_len(tmp_path):
    db = MetadataDB(str(tmp_path))
    _seed(db)
    assert db.downloaded_count() == len(db.closed_artwork_set()) == 3
    db.close()


def test_cache_hit_avoids_recompute(tmp_path, monkeypatch):
    db = MetadataDB(str(tmp_path))
    _seed(db)

    calls = {"n": 0}
    real = db._compute_closed_artwork_set

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(db, "_compute_closed_artwork_set", counting)

    first = db.closed_artwork_set()
    second = db.closed_artwork_set()
    assert first == second == {"10001", "20002", "30003"}
    assert calls["n"] == 1, "second call (no DB write) must hit the cache"
    db.close()


def test_cache_invalidates_after_write(tmp_path):
    db = MetadataDB(str(tmp_path))
    _seed(db)
    assert db.closed_artwork_set() == {"10001", "20002", "30003"}
    # A write appends to the WAL -> file signature changes -> recompute.
    db.import_downloaded_set(["99999"])
    assert "99999" in db.closed_artwork_set()
    db.close()


def test_returned_set_is_independent_copy(tmp_path):
    db = MetadataDB(str(tmp_path))
    _seed(db)
    s = db.closed_artwork_set()
    s.add("hacked")
    # Mutating the returned set must not corrupt the cached copy.
    assert "hacked" not in db.closed_artwork_set()
    db.close()


def test_signature_changes_with_writes(tmp_path):
    db = MetadataDB(str(tmp_path))
    _seed(db)
    sig_before = mdb._db_file_signature(db._path)
    db.import_downloaded_set(["12321"])
    sig_after = mdb._db_file_signature(db._path)
    assert sig_before != sig_after
    db.close()
