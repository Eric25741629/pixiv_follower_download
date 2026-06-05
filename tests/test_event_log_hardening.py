"""Tests for the event-log hardening work (recovery speed + data safety +
bounded resource use): reverse reader, O(tail) recover_tail, checkpoint anchor,
size rotation, byte-capped retention, post-snapshot compaction, batched fsync,
and the pages.downloaded_bulk recovery-correctness fix.
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.core.event_log as el
from app.core.event_log import (
    EventLog,
    recover_tail,
    _iter_events,
    _iter_lines_reverse,
    _parse_event_filename,
    _sorted_event_files,
)
from app.core.metadata_db import MetadataDB


def _events_dir(tmp_path):
    d = tmp_path / "events"
    d.mkdir(exist_ok=True)
    return d


def _write_lines(path, events):
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )


# ── reverse block reader ──────────────────────────────────────────────────────

def test_reverse_reader_reassembles_lines_longer_than_block(tmp_path):
    p = tmp_path / "f.txt"
    long = "X" * 200_000  # far larger than the 64 KB default block
    # Binary write with explicit \n so the assertion is OS-newline agnostic.
    p.write_bytes(f"first\n{long}\nlast\n".encode("utf-8"))
    # Consumers (.strip() each line before json.loads); mirror that here.
    lines = [ln.strip() for ln in _iter_lines_reverse(str(p), block_size=4096) if ln.strip()]
    assert lines[0] == "last"
    assert lines[1] == long
    assert lines[2] == "first"


def test_reverse_reader_no_trailing_newline(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"a\nb\nc")  # no final newline
    lines = [ln.strip() for ln in _iter_lines_reverse(str(p)) if ln.strip()]
    assert lines == ["c", "b", "a"]


# ── filename parsing / chronological sort ─────────────────────────────────────

def test_sequenced_files_sort_chronologically_not_lexically(tmp_path):
    d = _events_dir(tmp_path)
    for name in ("events-20260101.jsonl", "events-20260101.001.jsonl",
                 "events-20260101.002.jsonl", "events-20260102.jsonl"):
        (d / name).write_text("{}\n", encoding="utf-8")
    ordered = _sorted_event_files(str(d))
    # bare (.000) before .001 before .002, then next day — NOT lexical ('.' < 'j')
    assert ordered == [
        "events-20260101.jsonl",
        "events-20260101.001.jsonl",
        "events-20260101.002.jsonl",
        "events-20260102.jsonl",
    ]


def test_parse_bare_is_sequence_zero(tmp_path):
    assert _parse_event_filename("events-20260101.jsonl") == ("20260101", 0)
    assert _parse_event_filename("events-20260101.003.jsonl") == ("20260101", 3)
    assert _parse_event_filename("not-an-event.txt") is None


# ── _detect_unclean: bounded scan ─────────────────────────────────────────────

def test_detect_unclean_scan_cap_returns_true(tmp_path, monkeypatch):
    monkeypatch.setattr(el, "_DETECT_MAX_SCAN_BYTES", 200)
    d = _events_dir(tmp_path)
    # A clean shutdown sits at the START, then >200 bytes of data after it, so
    # the bounded tail scan never reaches the anchor -> conservatively unclean.
    events = [{"t": "2026-01-01T00:00:00.000", "k": "session.shutdown"}]
    events += [
        {"t": "2026-01-01T00:00:01.000", "k": "page.upsert",
         "pid": str(i), "page_index": 0, "status": "pending"}
        for i in range(60)
    ]
    _write_lines(d / "events-20260101.jsonl", events)
    log = EventLog(str(tmp_path))
    try:
        assert log.last_session_was_unclean is True
    finally:
        log.close()


# ── recover_tail: O(tail) correctness ─────────────────────────────────────────

def test_recover_tail_anchor_in_older_file(tmp_path):
    d = _events_dir(tmp_path)
    _write_lines(d / "events-20260101.jsonl", [
        {"t": "2026-01-01T00:00:00.000", "k": "session.start"},
        {"t": "2026-01-01T00:00:01.000", "k": "artwork.upsert", "pid": "100", "page_count": 1},
        {"t": "2026-01-01T00:00:02.000", "k": "session.shutdown"},
    ])
    _write_lines(d / "events-20260102.jsonl", [
        {"t": "2026-01-02T00:00:00.000", "k": "session.start"},
        {"t": "2026-01-02T00:00:01.000", "k": "artwork.upsert", "pid": "200", "page_count": 1},
    ])
    db = MetadataDB(str(tmp_path))
    try:
        n = recover_tail(db, str(d))
        assert n == 1  # only the orphan after the last anchor (older file's shutdown)
        assert db.get_artwork("200") is not None
        assert db.get_artwork("100") is None  # before the cutoff -> not re-applied
    finally:
        db.close()


def test_recover_tail_tolerates_torn_last_line(tmp_path):
    d = _events_dir(tmp_path)
    good = [
        {"t": "2026-01-02T00:00:00.000", "k": "session.start"},
        {"t": "2026-01-02T00:00:01.000", "k": "artwork.upsert", "pid": "7", "page_count": 1},
    ]
    content = "\n".join(json.dumps(e) for e in good) + "\n"
    content += '{"t": "2026-01-02T00:00:02.000", "k": "artwork.upse'  # torn, no newline
    (d / "events-20260102.jsonl").write_text(content, encoding="utf-8")
    db = MetadataDB(str(tmp_path))
    try:
        n = recover_tail(db, str(d))  # must not raise on the partial JSON
        assert n == 1
        assert db.get_artwork("7") is not None
    finally:
        db.close()


def test_checkpoint_anchor_bounds_recovery(tmp_path):
    log = EventLog(str(tmp_path))
    db = MetadataDB(str(tmp_path), event_log=log)
    db.upsert_artwork("1", page_count=1)
    log.emit("checkpoint", pid=123)          # startup anchor
    db.upsert_artwork("2", page_count=1)
    db.close()
    log._fh.close(); log._fh = None          # simulate crash (no shutdown)

    log2 = EventLog(str(tmp_path))
    db2 = MetadataDB(str(tmp_path), event_log=log2)
    try:
        n = recover_tail(db2, log2.log_dir)
        assert n == 1  # only artwork 2 (after the checkpoint) is an orphan
    finally:
        db2.close(); log2.close()


# ── size rotation ─────────────────────────────────────────────────────────────

def test_size_rotation_creates_sequenced_files_and_replays_in_order(tmp_path):
    log = EventLog(str(tmp_path), rotate_size_bytes=200, fsync_every_n=10000)
    for i in range(50):
        log.emit("page.upsert", pid=str(i), page_index=0, status="pending")
    log.close()
    d = tmp_path / "events"
    files = _sorted_event_files(str(d))
    assert len(files) >= 2  # rotated within the day
    keys = [_parse_event_filename(f) for f in files]
    assert keys == sorted(keys)  # chronological == parsed-sorted
    kinds = [e["k"] for e in _iter_events(str(d))]
    assert kinds.count("page.upsert") == 50  # nothing lost across rotation


# ── byte-capped retention + compaction ────────────────────────────────────────

def test_byte_cap_evicts_oldest_keeps_anchor_and_current(tmp_path):
    d = _events_dir(tmp_path)
    _write_lines(d / "events-20260101.jsonl", [
        {"t": "2026-01-01T00:00:00.000", "k": "artwork.upsert", "pid": f"1{i}", "page_count": 1}
        for i in range(20)
    ])
    _write_lines(d / "events-20260102.jsonl", [
        {"t": "2026-01-02T00:00:00.000", "k": "snapshot", "backup_path": "x"},
    ])
    _write_lines(d / "events-20260103.jsonl", [
        {"t": "2026-01-03T00:00:00.000", "k": "artwork.upsert", "pid": f"3{i}", "page_count": 1}
        for i in range(20)
    ])
    # retention_days=0 disables time-GC; tiny byte cap forces oldest eviction.
    log = EventLog(str(tmp_path), max_total_bytes=50, retention_days=0)
    try:
        remaining = set(os.listdir(d))
        assert "events-20260101.jsonl" not in remaining   # oldest, before anchor
        assert "events-20260102.jsonl" in remaining        # anchor file, protected
        assert "events-20260103.jsonl" in remaining        # newer than anchor, protected
    finally:
        log.close()


def test_compact_before_date_keeps_anchor_and_current(tmp_path):
    d = _events_dir(tmp_path)
    _write_lines(d / "events-20251201.jsonl", [
        {"t": "2025-12-01T00:00:00.000", "k": "artwork.upsert", "pid": "1", "page_count": 1},
    ])
    _write_lines(d / "events-20251202.jsonl", [
        {"t": "2025-12-02T00:00:00.000", "k": "snapshot", "backup_path": "x"},
    ])
    log = EventLog(str(tmp_path), retention_days=0)
    try:
        removed = log.compact_before_date("20251202")
        remaining = set(os.listdir(d))
        assert removed == 1
        assert "events-20251201.jsonl" not in remaining   # fully predates snapshot
        assert "events-20251202.jsonl" in remaining        # the anchor file
    finally:
        log.close()


# ── batched fsync durability ──────────────────────────────────────────────────

def test_batched_fsync_lines_flushed_and_readable_before_fsync(tmp_path):
    # High threshold => no per-line fsync, but flush() still lands bytes on disk
    # so a process kill never loses them (the durability tier we rely on).
    log = EventLog(str(tmp_path), fsync_every_n=10000, fsync_interval_sec=0.0)
    try:
        log.emit("page.upsert", pid="5", page_index=0, status="pending")
        d = tmp_path / "events"
        files = _sorted_event_files(str(d))
        content = (d / files[-1]).read_text(encoding="utf-8")
        assert '"pid": "5"' in content and '"k": "page.upsert"' in content
    finally:
        log.close()


# ── QW4: pages.downloaded_bulk replay flips pending -> downloaded ──────────────

def test_downloaded_bulk_replay_flips_pending_to_downloaded(tmp_path):
    """Regression for the latent bug: completed Step-4 pages must come back
    DOWNLOADED after crash recovery, not stuck 'pending' (which re-queued them).
    """
    log = EventLog(str(tmp_path))
    db = MetadataDB(str(tmp_path), event_log=log)
    url = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/9001_p0.jpg"
    db.upsert_pending_urls([(url, "9001")])
    db.mark_urls_done([url])
    db.close()
    log._fh.close(); log._fh = None  # crash

    # Wipe the DB so recovery must rebuild page status purely from the log.
    for ext in ("", "-wal", "-shm"):
        p = os.path.join(tmp_path, "metadata.sqlite3" + ext)
        if os.path.exists(p):
            os.remove(p)

    log2 = EventLog(str(tmp_path))
    db2 = MetadataDB(str(tmp_path), event_log=log2)
    try:
        recover_tail(db2, log2.log_dir)
        assert db2.pending_url_count() == 0           # not stuck pending
        page = db2.get_page("9001", 0)
        assert page is not None and page["status"] == "downloaded"
    finally:
        db2.close(); log2.close()
