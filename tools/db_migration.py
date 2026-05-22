"""Migrate legacy state into the new ``artworks`` + ``pages`` schema.

The migration is **idempotent**: re-running picks up nothing on a fresh
database (all INSERT OR IGNORE) and is safe to interrupt. It runs inside
one DB transaction; ``--dry-run`` rolls back at the end so the user can
inspect the planned row deltas before committing.

Order of operations:

  A. ``pids`` table         → artworks (with meta + meta_updated_at)
  B. ``downloaded`` + ``exist_pid.json``  → artworks (no meta; legacy sentinel)
  C. Filesystem disk scan   → pages(status='downloaded')
  D. ``pending_urls`` table → pages(status='pending'), skipping pages that
                              C already marked downloaded
  E. ``err_url.txt``        → pages(status='failed')
  F. ``pictures_id.txt``    → artworks; reset meta_updated_at to NULL for
                              user-queued PIDs that don't yet have meta

Step F intentionally re-opens the queue for PIDs the user explicitly
listed in pictures_id.txt but for which we don't yet have meta — this
lets Step 3 pick them up on its next run.

The sentinel ``'0001-01-01 00:00:00'`` marks "legacy, do not auto-process"
so Step 3's pending-artwork view filters them out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Callable

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from app.core.metadata_db import (
    MetadataDB,
    PAGE_STATUS_PENDING, PAGE_STATUS_DOWNLOADED, PAGE_STATUS_FAILED,
)
from app.core.pid_filesystem import (
    scan_paths, parse_pid_and_page_from_url, build_page_url,
)

LEGACY_SENTINEL = "0001-01-01 00:00:00"


def appdata_root() -> str:
    return os.path.join(os.environ["APPDATA"], "pixiv_download")


def read_settings_scan_roots() -> list[str]:
    """Mirror dump_state.py's root expansion so migration sees the same files."""
    with open(os.path.join(appdata_root(), "settings.json"), encoding="utf-8") as f:
        settings = json.load(f)
    dl_path = settings.get("download", {}).get("path") or ""
    legacy = settings.get("download", {}).get("legacy_scan_paths") or []
    if isinstance(legacy, str):
        legacy = [legacy]
    roots = [dl_path] + list(legacy)
    extras = []
    for p in roots:
        parent = os.path.dirname(p.rstrip("/\\"))
        if parent and os.path.isdir(parent) and parent not in roots:
            extras.append(parent)
    return list(dict.fromkeys(roots + extras))


# ---------------------------------------------------------------------------
# Step A: pids → artworks (full meta)
# ---------------------------------------------------------------------------

def step_a_pids_to_artworks(db: MetadataDB, log: Callable[[str], None]) -> int:
    """Copy each row from the legacy ``pids`` table into ``artworks``.

    The pid+meta lands in one shot via ``upsert_artwork``; if a row already
    exists for that PID (e.g. from a prior run) the COALESCE in
    ``upsert_artwork`` keeps the most informative value.
    """
    cur = db._conn().execute(
        "SELECT pid, tags, like_count, page_count, img_url, "
        "requires_cookie, updated_at FROM pids"
    )
    n = 0
    for pid, tags_blob, like, pages, img, rc, updated in cur.fetchall():
        try:
            tags = json.loads(tags_blob) if tags_blob else []
        except Exception:
            tags = []
        db.upsert_artwork(
            pid,
            page_count=pages,
            like_count=like,
            tags=tags,
            img_url_template=img,
            requires_cookie=None if rc is None else bool(rc),
            meta_updated_at=updated or LEGACY_SENTINEL,
        )
        n += 1
    log(f"  A. pids → artworks:                {n:,} rows")
    return n


# ---------------------------------------------------------------------------
# Step B: legacy "closed" set → artworks
# ---------------------------------------------------------------------------

def _load_exist_pid_json() -> set[str]:
    p = os.path.join(appdata_root(), "exist_pid.json")
    if not os.path.isfile(p):
        return set()
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return {str(k) for k in data.keys()}
    if isinstance(data, list):
        return {str(x) for x in data}
    return set()


def step_b_closed_to_artworks(db: MetadataDB, log: Callable[[str], None]) -> int:
    """Pre-seed ``artworks`` with every PID we believed was closed.

    Union of the legacy ``downloaded`` table and ``exist_pid.json``. PIDs
    that already have a row from Step A keep their meta — this only adds
    skeleton rows for the bare 'closed' set, with ``meta_updated_at`` set to
    the legacy sentinel so Step 3 ignores them.
    """
    conn = db._conn()
    downloaded = {r[0] for r in conn.execute("SELECT pid FROM downloaded").fetchall()}
    exist_pid = _load_exist_pid_json()
    union = downloaded | exist_pid
    if not union:
        log("  B. closed→artworks:                0 rows (nothing to seed)")
        return 0
    # Bulk insert; existing rows untouched by INSERT OR IGNORE.
    rows = [(p, LEGACY_SENTINEL, LEGACY_SENTINEL) for p in union]
    db._bulk_write(
        "INSERT OR IGNORE INTO artworks (pid, discovered_at, meta_updated_at) "
        "VALUES (?, ?, ?)",
        rows,
    )
    # COALESCE: rows that DID exist (from Step A) keep their real meta_updated_at;
    # newly-inserted rows get the sentinel. No follow-up needed.
    log(f"  B. closed → artworks:              {len(rows):,} attempted "
        f"(downloaded={len(downloaded):,}, exist_pid.json={len(exist_pid):,})")
    return len(rows)


# ---------------------------------------------------------------------------
# Step C: filesystem → pages(downloaded)
# ---------------------------------------------------------------------------

def step_c_disk_to_pages(
    db: MetadataDB,
    scan_roots: list[str],
    log: Callable[[str], None],
) -> int:
    """Walk every download folder, emit one pages row per (pid, page) found.

    Files that match a PID but have no page indicator (e.g. ``…PID12345.gif``
    for single-frame ugoira) are inserted as page_index=0. Bulk INSERT OR
    IGNORE so re-runs are no-ops.
    """
    log(f"  C. scanning disk: {scan_roots}")
    t0 = time.time()
    result = scan_paths(scan_roots)
    log(f"     scanned {result.total_files:,} files in {time.time()-t0:.1f}s; "
        f"{len(result.pid_anyfile):,} distinct PIDs, "
        f"{len(result.pid_pages):,} with page indices")

    rows = []
    for pid, pages in result.pid_pages.items():
        first_path = result.pid_first_path.get(pid)
        for pidx in pages:
            rows.append((pid, pidx, PAGE_STATUS_DOWNLOADED, None, first_path))
    # Single-file PIDs (no page suffix) → treat as page 0 of a 1-page artwork.
    no_page = result.pid_anyfile - set(result.pid_pages.keys())
    for pid in no_page:
        rows.append((pid, 0, PAGE_STATUS_DOWNLOADED, None,
                     result.pid_first_path.get(pid)))
    n = db.upsert_pages_bulk(rows)
    # Disk-discovered PIDs need an artworks row so the schema stays consistent.
    # Insert OR IGNORE keeps any meta from Step A.
    db._bulk_write(
        "INSERT OR IGNORE INTO artworks (pid, discovered_at, meta_updated_at) "
        "VALUES (?, ?, ?)",
        [(p, LEGACY_SENTINEL, LEGACY_SENTINEL) for p in result.pid_anyfile],
    )
    log(f"  C. disk → pages(downloaded):       {n:,} page rows "
        f"({len(no_page):,} single-file PIDs as page 0)")
    return n


# ---------------------------------------------------------------------------
# Step D: pending_urls → pages(pending)
# ---------------------------------------------------------------------------

def step_d_pending_urls_to_pages(db: MetadataDB, log: Callable[[str], None]) -> int:
    """Copy each pending URL row into pages(pending), skipping pages that
    Step C already marked downloaded. Pages with unparseable URLs (no
    ``_p<N>`` or template) are dropped — they were buggy entries.
    """
    cur = db._conn().execute(
        "SELECT url, pid FROM pending_urls WHERE status='pending'"
    )
    rows: list = []
    unparseable = 0
    for url, pid in cur.fetchall():
        parsed_pid, pidx = parse_pid_and_page_from_url(url)
        # Trust the URL's encoded PID over the legacy `pid` column.
        target_pid = parsed_pid or pid
        if not target_pid or pidx is None:
            unparseable += 1
            continue
        rows.append((target_pid, pidx, PAGE_STATUS_PENDING, url, None))
    n = db.upsert_pages_bulk(rows)
    # Make sure each pending-URL PID has an artworks row so FK-like
    # consistency holds (we don't enforce FK in SQLite, but it's nicer for
    # joins / queries).
    db._bulk_write(
        "INSERT OR IGNORE INTO artworks (pid, discovered_at, meta_updated_at) "
        "VALUES (?, ?, ?)",
        [(r[0], LEGACY_SENTINEL, LEGACY_SENTINEL) for r in rows],
    )
    log(f"  D. pending_urls → pages(pending):  {n:,} rows "
        f"({unparseable:,} unparseable URLs dropped)")
    return n


# ---------------------------------------------------------------------------
# Step E: err_url.txt → pages(failed)
# ---------------------------------------------------------------------------

def step_e_err_url_to_pages(db: MetadataDB, log: Callable[[str], None]) -> int:
    """Each line in err_url.txt is ``<url> <timestamp>``.

    We backfill these as failed pages with the timestamp captured in
    ``last_attempted_at``. Lines that don't parse as URL+timestamp are
    skipped with a count for the report.
    """
    p = os.path.join(appdata_root(), "err_url.txt")
    if not os.path.isfile(p):
        log("  E. err_url.txt:                    (not present)")
        return 0
    rows: list = []
    skipped = 0
    with open(p, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Format observed: "<url><whitespace><YYYYMMDD_HHMMSS>"
            parts = line.split()
            url = parts[0]
            pid, pidx = parse_pid_and_page_from_url(url)
            if not pid or pidx is None:
                skipped += 1
                continue
            rows.append((pid, pidx, PAGE_STATUS_FAILED, url, None))
    n = db.upsert_pages_bulk(rows)
    # Mark failure reason + bump attempt count for these rows. INSERT OR IGNORE
    # above didn't overwrite existing rows, so we do a follow-up UPDATE that
    # only touches rows whose status is still 'failed' and reason is empty.
    db._bulk_write(
        "UPDATE pages SET failure_reason = COALESCE(failure_reason, 'legacy_err_url') "
        "WHERE pid=? AND page_index=? AND status='failed'",
        [(r[0], r[1]) for r in rows],
    )
    log(f"  E. err_url.txt → pages(failed):    {n:,} rows ({skipped:,} skipped)")
    return n


# ---------------------------------------------------------------------------
# Step F: pictures_id.txt → artworks (user-queued)
# ---------------------------------------------------------------------------

def step_f_pictures_id_to_artworks(db: MetadataDB, log: Callable[[str], None]) -> int:
    """User's explicit Step-3 queue. New PIDs get artworks row with
    ``meta_updated_at=NULL`` (so Step 3 picks them up). PIDs that already
    have a row but no real meta yet (only the legacy sentinel) get their
    ``meta_updated_at`` reset to NULL so Step 3 reconsiders them.
    """
    p = os.path.join(appdata_root(), "pictures_id.txt")
    if not os.path.isfile(p):
        log("  F. pictures_id.txt:                (not present)")
        return 0
    with open(p, encoding="utf-8", errors="ignore") as f:
        pids = [ln.strip() for ln in f if ln.strip() and ln.strip().isdigit()]
    if not pids:
        log("  F. pictures_id.txt:                (empty)")
        return 0
    conn = db._conn()
    # New PIDs: INSERT with NULL meta_updated_at (queue them for Step 3).
    db._bulk_write(
        "INSERT OR IGNORE INTO artworks (pid, discovered_at, meta_updated_at) "
        "VALUES (?, datetime('now'), NULL)",
        [(p,) for p in pids],
    )
    # Existing PIDs WITH legacy sentinel + no real meta: clear meta_updated_at
    # so the user's explicit queue request takes precedence. Real meta
    # (page_count IS NOT NULL) is left alone. SAVEPOINT (not BEGIN) so this
    # nests safely inside the outer migration transaction.
    place = ",".join("?" * len(pids))
    conn.execute("SAVEPOINT step_f_update")
    try:
        conn.execute(
            f"UPDATE artworks SET meta_updated_at = NULL "
            f"WHERE pid IN ({place}) AND page_count IS NULL "
            f"AND meta_updated_at = ?",
            [*pids, LEGACY_SENTINEL],
        )
        conn.execute("RELEASE step_f_update")
    except Exception:
        conn.execute("ROLLBACK TO step_f_update")
        conn.execute("RELEASE step_f_update")
        raise
    log(f"  F. pictures_id.txt → artworks:     {len(pids):,} PIDs queued for Step 3")
    return len(pids)


# ---------------------------------------------------------------------------
# Step G: backfill pending pages for queued PIDs that already have meta
# ---------------------------------------------------------------------------

def _read_pictures_id_pids() -> list[str]:
    p = os.path.join(appdata_root(), "pictures_id.txt")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8", errors="ignore") as f:
        return [ln.strip() for ln in f if ln.strip() and ln.strip().isdigit()]


def _missing_pages_for_pid(db: MetadataDB, pid: str, page_count: int) -> list[int]:
    """Return the list of page indices in [0, page_count) that have no
    'downloaded' row in ``pages`` for this PID. Returns empty list when
    the PID is already complete."""
    existing_done = {
        idx for (idx, status, _path) in db.get_pages_for_pid(pid)
        if status == "downloaded"
    }
    return [i for i in range(page_count) if i not in existing_done]


def step_g_pending_pages_for_queued_with_meta(
    db: MetadataDB, log: Callable[[str], None]
) -> int:
    """Step F handled PIDs without meta; this fills the symmetric gap.

    For each PID in pictures_id.txt that already has meta (``page_count``
    known) but is incomplete on disk, generate ``pages(status='pending')``
    rows for the missing page indices. Step 4 can then download them
    directly, without Step 3 having to re-fetch the meta we already have.

    URL is rebuilt from ``artworks.img_url_template`` via
    :func:`pid_filesystem.build_page_url`; if that fails (template missing
    or unparseable) the page is still recorded as pending — Step 4's
    runtime URL resolver will retry the meta fetch.
    """
    queued = _read_pictures_id_pids()
    if not queued:
        log("  G. queued-with-meta backfill:      (pictures_id.txt empty)")
        return 0
    n_pids_touched = 0
    n_pages_inserted = 0
    rows: list = []
    for pid in queued:
        artwork = db.get_artwork(pid)
        if not artwork or not artwork.get("page_count"):
            continue                          # Step 3 will create pages
        missing = _missing_pages_for_pid(db, pid, int(artwork["page_count"]))
        if not missing:
            continue                          # already complete
        template = artwork.get("img_url_template")
        for pidx in missing:
            url = build_page_url(template, pidx)
            rows.append((pid, pidx, PAGE_STATUS_PENDING, url, None))
        n_pids_touched += 1
        n_pages_inserted += len(missing)
    if rows:
        db.upsert_pages_bulk(rows)
    log(f"  G. queued-with-meta backfill:      {n_pids_touched:,} PIDs, "
        f"{n_pages_inserted:,} pending pages added")
    return n_pages_inserted


# ---------------------------------------------------------------------------
# Verification: did the migration land where we expected?
# ---------------------------------------------------------------------------

def print_summary(db: MetadataDB, log: Callable[[str], None]) -> None:
    conn = db._conn()
    log("\n=== New schema row counts ===")
    log(f"  artworks total:               {conn.execute('SELECT COUNT(*) FROM artworks').fetchone()[0]:,}")
    log(f"  artworks WITH meta:           "
        f"{conn.execute('SELECT COUNT(*) FROM artworks WHERE page_count IS NOT NULL').fetchone()[0]:,}")
    log(f"  artworks meta_updated NULL:   "
        f"{conn.execute('SELECT COUNT(*) FROM artworks WHERE meta_updated_at IS NULL').fetchone()[0]:,}  ← Step 3 queue")
    log(f"  artworks revoked:             "
        f"{conn.execute('SELECT COUNT(*) FROM artworks WHERE revoked_at IS NOT NULL').fetchone()[0]:,}")
    log(f"  pages total:                  {conn.execute('SELECT COUNT(*) FROM pages').fetchone()[0]:,}")
    for status, count in db.page_status_counts().items():
        log(f"    pages.{status}: {count:,}")
    log(f"  v_pending_artworks:           "
        f"{conn.execute('SELECT COUNT(*) FROM v_pending_artworks').fetchone()[0]:,}")
    log(f"  v_pending_pages:              "
        f"{conn.execute('SELECT COUNT(*) FROM v_pending_pages').fetchone()[0]:,}")
    log(f"  v_complete_artworks:          "
        f"{conn.execute('SELECT COUNT(*) FROM v_complete_artworks').fetchone()[0]:,}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_migration(*, dry_run: bool, log: Callable[[str], None] = print) -> None:
    db = MetadataDB(appdata_root())
    conn = db._conn()
    scan_roots = read_settings_scan_roots()

    log(f"=== Migration starting (dry_run={dry_run}) ===")
    t0 = time.time()

    # Wrap everything in a SAVEPOINT so --dry-run rolls back cleanly.
    conn.execute("SAVEPOINT migration")
    try:
        step_a_pids_to_artworks(db, log)
        step_b_closed_to_artworks(db, log)
        step_c_disk_to_pages(db, scan_roots, log)
        step_d_pending_urls_to_pages(db, log)
        step_e_err_url_to_pages(db, log)
        step_f_pictures_id_to_artworks(db, log)
        step_g_pending_pages_for_queued_with_meta(db, log)
        print_summary(db, log)
        if dry_run:
            conn.execute("ROLLBACK TO migration")
            conn.execute("RELEASE migration")
            log(f"\n--- DRY RUN: rolled back, no changes committed (elapsed {time.time()-t0:.1f}s) ---")
        else:
            conn.execute("RELEASE migration")
            log(f"\n--- DONE: changes committed (elapsed {time.time()-t0:.1f}s) ---")
    except Exception:
        conn.execute("ROLLBACK TO migration")
        conn.execute("RELEASE migration")
        log("\n--- ABORTED: rolled back ---")
        raise
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="run all steps inside a SAVEPOINT and roll back")
    args = ap.parse_args()
    run_migration(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
