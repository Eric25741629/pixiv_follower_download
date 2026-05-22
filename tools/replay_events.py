"""CLI: rebuild metadata.sqlite3 from a snapshot + events log.

Usage:
    python tools/replay_events.py [--target PATH] [--from-snapshot PATH] [--dry-run]

Defaults:
    --target          %APPDATA%/pixiv_download/metadata.sqlite3.rebuilt
    --from-snapshot   latest file in %APPDATA%/pixiv_download/history/
"""
from __future__ import annotations

import argparse
import os
import sys


def _default_base() -> str:
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "pixiv_download")


def _latest_snapshot(history_dir: str) -> str | None:
    if not os.path.isdir(history_dir):
        return None
    candidates = [
        os.path.join(history_dir, n)
        for n in os.listdir(history_dir)
        if n.startswith("metadata.sqlite3.")
    ]
    if not candidates:
        return None
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Replay event log into a fresh DB.")
    p.add_argument("--target", default=None,
                   help="Path to write the rebuilt DB (default: <appdata>/pixiv_download/metadata.sqlite3.rebuilt)")
    p.add_argument("--from-snapshot", default=None,
                   help="Path to a snapshot to restore first (default: latest in history/)")
    p.add_argument("--dry-run", action="store_true",
                   help="Count events without writing the DB")
    args = p.parse_args(argv)

    # Repo-root on sys.path so app.* imports work.
    here = os.path.abspath(os.path.dirname(__file__))
    repo_root = os.path.dirname(here)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from app.core.event_log import replay
    from app.core.metadata_db import DB_FILENAME

    base = _default_base()
    target = args.target or os.path.join(base, "metadata.sqlite3.rebuilt")
    log_dir = os.path.join(base, "events")
    snapshot = args.from_snapshot or _latest_snapshot(os.path.join(base, "history"))

    print(f"target:        {target}")
    print(f"log_dir:       {log_dir}")
    print(f"snapshot:      {snapshot or '(none — replaying from start)'}")
    print(f"dry-run:       {args.dry_run}")

    # `replay()` asserts that basename(target) == DB_FILENAME. If the user gave
    # us a custom basename, rename it to the standard name in the same dir.
    if os.path.basename(target) != DB_FILENAME:
        print(f"\nNote: rewriting --target basename to {DB_FILENAME!r} "
              f"(replay opens DB by directory).")
        target = os.path.join(os.path.dirname(target) or ".", DB_FILENAME)
        print(f"effective target: {target}")

    result = replay(target, log_dir, snapshot_path=snapshot, dry_run=args.dry_run)
    print(f"\napplied:                  {result.applied}")
    print(f"skipped_pre_snapshot:     {result.skipped_pre_snapshot}")
    print(f"errors:                   {len(result.errors)}")
    for e in result.errors[:20]:
        print(f"  - {e}")
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
