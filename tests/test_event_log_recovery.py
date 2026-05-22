import os

import pytest

from app.core.event_log import EventLog, recover_tail
from app.core.metadata_db import MetadataDB


def _simulate_crash(log: EventLog) -> None:
    """Close the file descriptor without writing session.shutdown."""
    log._fh.close()
    log._fh = None


def test_recover_tail_zero_on_clean_shutdown(tmp_path):
    log = EventLog(str(tmp_path))
    db = MetadataDB(str(tmp_path), event_log=log)
    db.upsert_artwork("1", page_count=1, meta_updated_at="2026-05-23 10:00:00")
    db.close()
    log.close()

    log2 = EventLog(str(tmp_path))
    db2 = MetadataDB(str(tmp_path), event_log=log2)
    try:
        n = recover_tail(db2, log2.log_dir)
        assert n == 0
    finally:
        db2.close()
        log2.close()


def test_recover_tail_applies_orphan_events(tmp_path):
    log = EventLog(str(tmp_path))
    db = MetadataDB(str(tmp_path), event_log=log)
    db.upsert_artwork("1", page_count=1, meta_updated_at="2026-05-23 10:00:00")
    db.mark_page_downloaded("1", 0, url="http://x")
    db.close()
    _simulate_crash(log)

    # delete the DB to simulate the case where DB lost the writes
    os.remove(os.path.join(tmp_path, "metadata.sqlite3"))
    for ext in (".sqlite3-wal", ".sqlite3-shm"):
        p = os.path.join(tmp_path, "metadata" + ext)
        if os.path.exists(p):
            os.remove(p)

    log2 = EventLog(str(tmp_path))
    db2 = MetadataDB(str(tmp_path), event_log=log2)
    try:
        assert log2.last_session_was_unclean is True
        n = recover_tail(db2, log2.log_dir)
        assert n >= 2  # upsert_artwork + mark_page_downloaded
        assert db2.get_artwork("1") is not None
    finally:
        db2.close()
        log2.close()


def test_recover_tail_is_idempotent(tmp_path):
    log = EventLog(str(tmp_path))
    db = MetadataDB(str(tmp_path), event_log=log)
    db.upsert_artwork("1", page_count=1, meta_updated_at="2026-05-23 10:00:00")
    db.close()
    _simulate_crash(log)

    log2 = EventLog(str(tmp_path))
    db2 = MetadataDB(str(tmp_path), event_log=log2)
    try:
        n1 = recover_tail(db2, log2.log_dir)
        n2 = recover_tail(db2, log2.log_dir)
        assert n2 == n1  # second call re-applies same events; idempotent at row level
        a = db2.get_artwork("1")
        assert a["page_count"] == 1
    finally:
        db2.close()
        log2.close()


def test_recover_tail_does_not_write_events(tmp_path):
    """Even without Task 6's MetadataDB emits, recover_tail itself must
    not append new events to the log (avoid emit loop)."""
    log = EventLog(str(tmp_path))
    db = MetadataDB(str(tmp_path), event_log=log)
    db.upsert_artwork("1", page_count=1, meta_updated_at="2026-05-23 10:00:00")
    _simulate_crash(log)

    log2 = EventLog(str(tmp_path))
    db2 = MetadataDB(str(tmp_path), event_log=log2)

    files = sorted(os.listdir(log2.log_dir))
    line_counts_before = {
        n: sum(1 for _ in open(os.path.join(log2.log_dir, n), encoding="utf-8"))
        for n in files
    }
    try:
        recover_tail(db2, log2.log_dir)
    finally:
        line_counts_after = {
            n: sum(1 for _ in open(os.path.join(log2.log_dir, n), encoding="utf-8"))
            for n in files
        }
        db2.close()
        log2.close()
    assert line_counts_after == line_counts_before
