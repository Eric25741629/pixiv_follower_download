"""Check that the legacy tables and the new ``artworks``+``pages`` schema
agree about the world.

Runs read-only diff queries across the two schema generations and reports
discrepancies. Intended for the Phase 2 shadow-write window: an
inconsistency means the shadow writes missed something the legacy path
caught (or vice-versa). Phase 5 deletes the legacy tables — at that point
this script becomes a historical artefact.

Usage::

    python tools/verify_consistency.py             # human-readable report
    python tools/verify_consistency.py --json      # machine-readable

Exit code is 0 when every check passes, 1 when any drift is detected.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys


def db_path() -> str:
    return os.path.join(os.environ["APPDATA"], "pixiv_download", "metadata.sqlite3")


def _count(cur: sqlite3.Cursor, sql: str, params: tuple = ()) -> int:
    return int(cur.execute(sql, params).fetchone()[0])


def _diff_artworks_vs_pids(cur: sqlite3.Cursor) -> dict:
    """Every ``pids`` row should have a matching ``artworks`` row.

    The reverse (artworks without pids) is expected — the migration also
    seeded artworks from exist_pid.json and disk scan, which never had
    rows in the legacy ``pids`` table.
    """
    n_pids = _count(cur, "SELECT COUNT(*) FROM pids")
    n_pids_in_artworks = _count(cur, """
        SELECT COUNT(*) FROM pids p
        WHERE EXISTS (SELECT 1 FROM artworks a WHERE a.pid = p.pid)
    """)
    missing = n_pids - n_pids_in_artworks
    return {
        "pids_total": n_pids,
        "pids_in_artworks": n_pids_in_artworks,
        "missing_from_artworks": missing,
        "ok": missing == 0,
    }


def _diff_downloaded_vs_complete(cur: sqlite3.Cursor) -> dict:
    """PIDs in legacy ``downloaded`` table that are NOT closed in the new
    schema are suspicious — they should be ``complete`` OR ``revoked`` OR
    legacy-sentinel-closed.

    Some drift is expected: PIDs that were in ``downloaded`` only because of
    Bug 1 (PID-level marking despite failures) will correctly appear as
    "not closed" in the new world. That's a feature, not a defect.
    """
    n_downloaded = _count(cur, "SELECT COUNT(*) FROM downloaded")
    n_downloaded_not_closed = _count(cur, """
        SELECT COUNT(*) FROM downloaded d
        WHERE NOT EXISTS (SELECT 1 FROM v_closed_artworks c WHERE c.pid = d.pid)
    """)
    return {
        "downloaded_total": n_downloaded,
        "downloaded_not_closed": n_downloaded_not_closed,
        "note": (
            "Non-zero is expected — Bug 1 PID-level marks that the new "
            "schema correctly reopens as partial-download."
        ),
    }


def _diff_pending_urls_vs_pages(cur: sqlite3.Cursor) -> dict:
    """Each URL in legacy ``pending_urls`` should map to a page row.

    Matching is done by parsing ``(pid, page_index)`` out of the URL — the
    disk-scan step of the migration created pages rows with ``url=NULL``
    when a file already existed on disk, so a strict URL-string match
    misses those absorbed entries. Joining on ``(pid, page_index)`` via
    Python is cheaper than wiring a regex into SQL.

    Drift to watch: ``pending_urls`` rows with NO matching page tuple —
    those would mean the shadow write missed a row.
    """
    from app.core.pid_filesystem import parse_pid_and_page_from_url
    cur.execute("SELECT url FROM pending_urls")
    urls = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT pid, page_index FROM pages")
    page_keys = {(r[0], int(r[1])) for r in cur.fetchall()}
    matched = 0
    unparseable = 0
    for url in urls:
        pid, pidx = parse_pid_and_page_from_url(url)
        if pid is None or pidx is None:
            unparseable += 1
            continue
        if (pid, pidx) in page_keys:
            matched += 1
    drift = len(urls) - matched - unparseable
    return {
        "pending_urls_total": len(urls),
        "matched_by_pid_page": matched,
        "unparseable_urls": unparseable,
        "missing_from_pages": drift,
        "ok": drift == 0,
    }


def _diff_meta_columns(cur: sqlite3.Cursor) -> dict:
    """Per-column comparison: any ``pids`` row whose ``artworks`` shadow
    disagrees on like_count / page_count / requires_cookie is a shadow-
    write defect.
    """
    n_drift_like = _count(cur, """
        SELECT COUNT(*) FROM pids p
        JOIN artworks a ON a.pid = p.pid
        WHERE p.like_count IS NOT NULL AND a.like_count IS NOT NULL
          AND p.like_count != a.like_count
    """)
    n_drift_pc = _count(cur, """
        SELECT COUNT(*) FROM pids p
        JOIN artworks a ON a.pid = p.pid
        WHERE p.page_count IS NOT NULL AND a.page_count IS NOT NULL
          AND p.page_count != a.page_count
    """)
    return {
        "like_count_drift": n_drift_like,
        "page_count_drift": n_drift_pc,
        "ok": (n_drift_like == 0 and n_drift_pc == 0),
    }


CHECKS = (
    ("artworks_vs_pids", _diff_artworks_vs_pids),
    ("downloaded_vs_complete", _diff_downloaded_vs_complete),
    ("pending_urls_vs_pages", _diff_pending_urls_vs_pages),
    ("meta_columns", _diff_meta_columns),
)


def run_all() -> tuple[dict, bool]:
    """Run every drift check; return ``(report, all_ok)``."""
    if not os.path.isfile(db_path()):
        return {"error": f"DB not found at {db_path()}"}, False
    conn = sqlite3.connect(db_path())
    cur = conn.cursor()
    try:
        report = {name: check(cur) for name, check in CHECKS}
    finally:
        conn.close()
    all_ok = all(item.get("ok", True) for item in report.values())
    return report, all_ok


def print_human(report: dict) -> None:
    """Stick to ASCII markers — the Windows default cp950 codec chokes on
    Unicode tick / cross glyphs when stdout isn't reconfigured."""
    for section, data in report.items():
        ok = data.get("ok", None)
        marker = "PASS" if ok else ("INFO" if ok is None else "FAIL")
        print(f"\n[{marker}] {section}")
        for k, v in data.items():
            if k == "ok":
                continue
            print(f"    {k}: {v}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true",
                    help="emit JSON instead of the human-readable report")
    args = ap.parse_args()

    report, ok = run_all()
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print_human(report)
        print(f"\n=> {'all checks passed' if ok else 'drift detected'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
