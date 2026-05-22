"""Regression tests for the new artworks + pages schema.

These tests pin down the behaviour we just fixed (Bug 1 + Bug 2) so the
schema can be refactored later without silently reintroducing the
PID-level "downloaded" semantics that caused failed pages to vanish.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB, PAGE_STATUS_PENDING


def _five_page_artwork(db, pid="12345"):
    """Set up a 5-page artwork with meta + all 5 pending URLs."""
    db.upsert_meta(
        pid, tags=["blue"], like_count=5000, page_count=5,
        img_url=f"https://i.pximg.net/.../{pid}_p.png",
        requires_cookie=False, updated_at="2026-05-20 12:00:00",
    )
    db.upsert_pending_urls([
        (f"https://i.pximg.net/img-original/img/2026/.../{pid}_p{i}.png", pid)
        for i in range(5)
    ])
    return pid


def test_partial_failure_leaves_pid_open(tmp_path):
    """Bug 1 regression: marking 3 of 5 pages downloaded must NOT close the PID.

    Under the legacy schema, ``_maybe_flush_exist_pid`` ran unconditionally
    after each PID group and the entire PID landed in ``downloaded``. The
    remaining pages were then permanently hidden by the SQL filter.
    """
    db = MetadataDB(str(tmp_path))
    pid = _five_page_artwork(db)
    db.mark_urls_done([
        f"https://i.pximg.net/img-original/img/2026/.../{pid}_p{i}.png"
        for i in range(3)
    ])
    assert pid not in db.complete_artwork_set()
    assert pid not in db.closed_artwork_set()
    assert db.page_status_counts() == {"downloaded": 3, "pending": 2}


def test_failed_pages_remain_visible_for_retry(tmp_path):
    """Bug 2 regression: pages still pending must be returned by the URL
    filter even if other pages of the same PID are already downloaded.
    """
    db = MetadataDB(str(tmp_path))
    pid = _five_page_artwork(db)
    db.mark_urls_done([
        f"https://i.pximg.net/img-original/img/2026/.../{pid}_p{i}.png"
        for i in range(3)
    ])
    pending = db.get_pending_urls_filtered(like_min=0)
    pids_in_pending = {p for _, p in pending}
    assert pid in pids_in_pending
    assert sum(1 for _, p in pending if p == pid) == 2


def test_completion_after_retry_closes_pid(tmp_path):
    """Once every page is downloaded, the PID enters v_complete_artworks
    AND v_closed_artworks automatically — no PID-level mark required.
    """
    db = MetadataDB(str(tmp_path))
    pid = _five_page_artwork(db)
    db.mark_urls_done([
        f"https://i.pximg.net/img-original/img/2026/.../{pid}_p{i}.png"
        for i in range(5)
    ])
    assert pid in db.complete_artwork_set()
    assert pid in db.closed_artwork_set()
    # And no leftover pending entries for it.
    pending = db.get_pending_urls_filtered(like_min=0)
    assert all(p != pid for _, p in pending)


def test_revoked_pid_is_closed_even_with_pending_pages(tmp_path):
    """``revoked_at`` short-circuits everything: a PID Pixiv removed
    should be skipped by Step 4 even if Step 3 already queued some URLs.
    """
    db = MetadataDB(str(tmp_path))
    pid = _five_page_artwork(db)
    db.mark_artwork_revoked(pid)
    assert pid in db.closed_artwork_set()
    assert db.is_pid_closed(pid)


def test_legacy_sentinel_closes_pid_only_with_no_pending(tmp_path):
    """Legacy-imported PIDs (sentinel meta_updated_at) close *iff* they
    have no pending pages — pending work overrides the legacy bypass.
    """
    db = MetadataDB(str(tmp_path))
    # Sentinel PID with no pending pages → closed
    db.upsert_artwork("70000001", meta_updated_at="0001-01-01 00:00:00")
    assert db.is_pid_closed("70000001")
    # Sentinel PID WITH a pending page → NOT closed (user requeued it)
    db.upsert_artwork("70000002", meta_updated_at="0001-01-01 00:00:00")
    db.upsert_page("70000002", 0, status=PAGE_STATUS_PENDING, url="x")
    assert not db.is_pid_closed("70000002")


def _insert_failed_page(db, pid, *, attempt_count, last_attempted_at, url=None):
    """Insert a single ``status='failed'`` row with exact bookkeeping.

    ``upsert_page`` always uses datetime('now') for last_attempted_at when
    asked to stamp it, so we go through a direct INSERT for these tests
    that need to pin the timestamp (NULL / 'now' / 'now - 25 hours').
    """
    page_url = url or f"https://i.pximg.net/.../{pid}_p0.png"
    with db._lock:
        db._conn().execute(
            "INSERT OR REPLACE INTO pages "
            "(pid, page_index, status, url, attempt_count, last_attempted_at, failure_reason) "
            f"VALUES (?, 0, 'failed', ?, ?, {last_attempted_at}, 'http timeout')",
            (pid, page_url, attempt_count),
        )


def test_retriable_failed_pages_returns_fresh_failures(tmp_path):
    """A failed page with attempt_count<max and no prior timestamp qualifies."""
    db = MetadataDB(str(tmp_path))
    _insert_failed_page(db, "80000001", attempt_count=1, last_attempted_at="NULL")
    rows = db.get_retriable_failed_pages()
    assert len(rows) == 1
    url, pid = rows[0]
    assert pid == "80000001"
    assert url.endswith("80000001_p0.png")


def test_retriable_failed_pages_excludes_exhausted_attempts(tmp_path):
    """attempt_count >= max_attempts must be filtered out."""
    db = MetadataDB(str(tmp_path))
    _insert_failed_page(db, "80000002", attempt_count=5, last_attempted_at="NULL")
    _insert_failed_page(db, "80000003", attempt_count=9, last_attempted_at="NULL")
    rows = db.get_retriable_failed_pages(max_attempts=5)
    assert rows == []


def test_retriable_failed_pages_excludes_within_cooldown(tmp_path):
    """A page that failed seconds ago must wait out the cooldown."""
    db = MetadataDB(str(tmp_path))
    _insert_failed_page(
        db, "80000004", attempt_count=1, last_attempted_at="datetime('now')",
    )
    rows = db.get_retriable_failed_pages(cooldown_hours=24)
    assert rows == []


def test_retriable_failed_pages_includes_after_cooldown(tmp_path):
    """A page whose last attempt is older than cooldown_hours is returned."""
    db = MetadataDB(str(tmp_path))
    _insert_failed_page(
        db, "80000005", attempt_count=2,
        last_attempted_at="datetime('now', '-25 hours')",
    )
    rows = db.get_retriable_failed_pages(cooldown_hours=24)
    assert len(rows) == 1
    assert rows[0][1] == "80000005"


def test_retriable_failed_pages_requires_url(tmp_path):
    """A failed page without a stored URL cannot be retried."""
    db = MetadataDB(str(tmp_path))
    with db._lock:
        db._conn().execute(
            "INSERT INTO pages "
            "(pid, page_index, status, url, attempt_count, last_attempted_at) "
            "VALUES ('80000006', 0, 'failed', NULL, 1, NULL)"
        )
    assert db.get_retriable_failed_pages() == []


def test_mark_artwork_revoked_persists_via_db(tmp_path):
    """Phase 7: a 404 detected at any call site must persist via
    ``mark_artwork_revoked`` so subsequent runs don't re-fetch the PID.
    """
    db = MetadataDB(str(tmp_path))
    pid = "11111111"
    db.upsert_artwork(pid)
    db.mark_artwork_revoked(pid)
    assert db.is_pid_closed(pid)
    artwork = db.get_artwork(pid)
    assert artwork is not None
    assert artwork["revoked_at"] is not None
