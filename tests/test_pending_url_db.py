"""Tests for the pending_urls and pending_pids tables added to MetadataDB.

Scenarios:
1. upsert_pending_urls stores entries; get_pending_urls returns them
2. INSERT OR IGNORE: re-inserting same URL does not overwrite status
3. mark_urls_done removes URLs from get_pending_urls
4. Filtered-out URLs (never marked done) remain visible to next run
5. upsert_pending_pids / get_pending_pids / mark_pid_done
6. import_pending_urls_from_file reads a fixture file
7. import_pending_pids_from_file reads a fixture file
8. url_row_count counts all rows regardless of status
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB


# ── 1. upsert_pending_urls + get_pending_urls ─────────────────────────────────

def test_upsert_and_get_pending_urls(tmp_path):
    db = MetadataDB(str(tmp_path))
    entries = [
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/111_p0.jpg", "111"),
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/222_p0.jpg", "222"),
    ]
    db.upsert_pending_urls(entries)
    rows = db.get_pending_urls()
    urls = [r[0] for r in rows]
    assert "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/111_p0.jpg" in urls
    assert "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/222_p0.jpg" in urls


def test_upsert_pending_urls_empty_is_noop(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_pending_urls([])
    assert db.pending_url_count() == 0


# ── 2. INSERT OR IGNORE: re-insert does not overwrite status ──────────────────

def test_mark_url_done_deletes_row(tmp_path):
    db = MetadataDB(str(tmp_path))
    url = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/333_p0.jpg"
    db.upsert_pending_urls([(url, "333")])
    db.mark_url_done(url)
    # Row deleted — table is empty
    assert db.url_row_count() == 0
    assert db.pending_url_count() == 0


# ── 3. mark_urls_done ─────────────────────────────────────────────────────────

def test_mark_urls_done_removes_from_pending(tmp_path):
    db = MetadataDB(str(tmp_path))
    url_a = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/444_p0.jpg"
    url_b = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/555_p0.jpg"
    db.upsert_pending_urls([(url_a, "444"), (url_b, "555")])
    db.mark_urls_done([url_a])
    pending = [r[0] for r in db.get_pending_urls()]
    assert url_a not in pending
    assert url_b in pending


def test_mark_urls_done_empty_list_is_noop(tmp_path):
    db = MetadataDB(str(tmp_path))
    url = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/666_p0.jpg"
    db.upsert_pending_urls([(url, "666")])
    db.mark_urls_done([])
    assert db.pending_url_count() == 1


# ── 4. Filtered-out URLs stay pending (core use-case) ─────────────────────────

def test_filtered_out_url_stays_pending_for_next_run(tmp_path):
    """Simulate: run 1 downloads url_a, skips url_b (filter); run 2 finds url_b."""
    db = MetadataDB(str(tmp_path))
    url_a = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/777_p0.jpg"
    url_b = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/888_p0.jpg"
    db.upsert_pending_urls([(url_a, "777"), (url_b, "888")])

    # Run 1: download url_a (deleted from table), skip url_b (still pending)
    db.mark_urls_done([url_a])

    # Run 2: url_b still visible, url_a is gone (deleted)
    pending = [r[0] for r in db.get_pending_urls()]
    assert url_b in pending
    assert url_a not in pending  # deleted, not just status-changed


# ── 5. pending_pids ───────────────────────────────────────────────────────────

def test_upsert_and_get_pending_pids(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_pending_pids(["100", "200", "300"])
    pids = db.get_pending_pids()
    assert "100" in pids
    assert "200" in pids
    assert "300" in pids


def test_mark_pid_done_removes_from_pending(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_pending_pids(["400", "500"])
    db.mark_pid_done("400")
    pids = db.get_pending_pids()
    assert "400" not in pids
    assert "500" in pids


def test_upsert_pending_pids_insert_or_ignore(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_pending_pids(["600"])
    db.mark_pid_done("600")
    db.upsert_pending_pids(["600"])  # must not revert to pending
    assert db.pending_pid_count() == 0


def test_upsert_pending_pids_empty_is_noop(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_pending_pids([])
    assert db.pending_pid_count() == 0


# ── 6. import_pending_urls_from_file ─────────────────────────────────────────

def test_import_pending_urls_from_file(tmp_path):
    url_file = tmp_path / "all_url.txt"
    url_file.write_text(
        "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/999_p0.jpg\n"
        "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/999_p1.jpg\n"
        "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/1001_p0.jpg\n",
        encoding="utf-8",
    )
    db = MetadataDB(str(tmp_path))
    n = db.import_pending_urls_from_file(str(url_file))
    assert n == 3
    pending = db.get_pending_urls()
    pids = {pid for _, pid in pending}
    assert "999" in pids
    assert "1001" in pids


def test_import_pending_urls_missing_file_returns_zero(tmp_path):
    db = MetadataDB(str(tmp_path))
    n = db.import_pending_urls_from_file(str(tmp_path / "nonexistent.txt"))
    assert n == 0


# ── 7. import_pending_pids_from_file ─────────────────────────────────────────

def test_import_pending_pids_from_file(tmp_path):
    pid_file = tmp_path / "pictures_id.txt"
    pid_file.write_text("12345\n67890\n11111\n", encoding="utf-8")
    db = MetadataDB(str(tmp_path))
    n = db.import_pending_pids_from_file(str(pid_file))
    assert n == 3
    pids = db.get_pending_pids()
    assert "12345" in pids
    assert "67890" in pids


def test_import_pending_pids_missing_file_returns_zero(tmp_path):
    db = MetadataDB(str(tmp_path))
    n = db.import_pending_pids_from_file(str(tmp_path / "nonexistent.txt"))
    assert n == 0


# ── 8. url_row_count ──────────────────────────────────────────────────────────

def test_url_row_count_after_delete(tmp_path):
    db = MetadataDB(str(tmp_path))
    url_a = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/2001_p0.jpg"
    url_b = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/2002_p0.jpg"
    db.upsert_pending_urls([(url_a, "2001"), (url_b, "2002")])
    db.mark_url_done(url_a)
    # url_a was deleted; only url_b remains
    assert db.url_row_count() == 1
    assert db.pending_url_count() == 1


# ── 9. get_pending_urls_filtered ────────────────────────────────────────────��─

def _setup_filtered_db(tmp_path):
    """Helper: insert 4 URLs with known metadata for filter tests."""
    db = MetadataDB(str(tmp_path))
    # pid 1001: likes=1500 — should always pass
    # pid 1002: likes=300  — fails likes>500, passes likes>100
    # pid 1003: likes=50   — fails all likes filters
    # pid 1004: no metadata — kept (IS NULL, may need network fetch)
    urls = [
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/1001_p0.jpg", "1001"),
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/1002_p0.jpg", "1002"),
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/1003_p0.jpg", "1003"),
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/1004_p0.jpg", "1004"),
    ]
    db.upsert_pending_urls(urls)
    db.upsert_meta("1001", like_count=1500)
    db.upsert_meta("1002", like_count=300)
    db.upsert_meta("1003", like_count=50)
    # 1004 intentionally has no metadata
    return db


def test_filtered_no_like_min_excludes_downloaded(tmp_path):
    db = _setup_filtered_db(tmp_path)
    # New schema: marking a URL done flips its page to status='downloaded',
    # which is what the filter excludes (per-page granularity). The legacy
    # ``mark_downloaded(pid)`` only touches the deprecated downloaded table
    # and isn't a reliable signal in the new world.
    db.mark_url_done("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/1001_p0.jpg")

    rows = db.get_pending_urls_filtered(like_min=0)
    pids = {pid for _, pid in rows}
    assert "1001" not in pids   # page downloaded → excluded
    assert "1002" in pids
    assert "1003" in pids
    assert "1004" in pids


def test_filtered_like_min_500_excludes_low_and_downloaded(tmp_path):
    db = _setup_filtered_db(tmp_path)
    db.mark_url_done("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/1001_p0.jpg")

    rows = db.get_pending_urls_filtered(like_min=500)
    pids = {pid for _, pid in rows}
    assert "1001" not in pids   # page downloaded → excluded
    assert "1002" not in pids   # likes=300 < 500
    assert "1003" not in pids   # likes=50 < 500
    assert "1004" in pids       # no metadata → kept (IS NULL)


def test_filtered_like_min_keeps_no_meta_urls(tmp_path):
    """PIDs with no metadata must survive the likes filter (IS NULL → keep)."""
    db = _setup_filtered_db(tmp_path)

    rows = db.get_pending_urls_filtered(like_min=9999)
    pids = {pid for _, pid in rows}
    assert "1004" in pids   # no metadata, must be kept


def test_filtered_like_min_100_keeps_300_likes(tmp_path):
    db = _setup_filtered_db(tmp_path)

    rows = db.get_pending_urls_filtered(like_min=100)
    pids = {pid for _, pid in rows}
    assert "1001" in pids   # 1500 >= 100
    assert "1002" in pids   # 300 >= 100
    assert "1003" not in pids  # 50 < 100
    assert "1004" in pids   # no meta → kept


# ── 10. _bulk_write transaction atomicity ─────────────────────────────────────

def test_bulk_write_inserts_all_rows_atomically(tmp_path):
    """A successful _bulk_write inserts ALL rows in one shot."""
    db = MetadataDB(str(tmp_path))
    db.upsert_pending_urls([
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/3001_p0.jpg", "3001"),
    ])
    initial_count = db.url_row_count()

    new_entries = [
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/4001_p0.jpg", "4001"),
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/4002_p0.jpg", "4002"),
        ("https://i.pximg.net/img-original/img/2024/01/01/12/00/00/4003_p0.jpg", "4003"),
    ]
    db.upsert_pending_urls(new_entries)
    assert db.url_row_count() == initial_count + 3


def test_mark_urls_done_bulk_is_single_transaction(tmp_path):
    """mark_urls_done uses _bulk_write; all updates land atomically."""
    db = MetadataDB(str(tmp_path))
    urls = [
        f"https://i.pximg.net/img-original/img/2024/01/01/12/00/00/500{i}_p0.jpg"
        for i in range(5)
    ]
    pids = [str(5000 + i) for i in range(5)]
    db.upsert_pending_urls(list(zip(urls, pids)))
    assert db.pending_url_count() == 5

    db.mark_urls_done(urls)
    assert db.pending_url_count() == 0
