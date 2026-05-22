"""Snapshot the current downloader state across all 7 storage sources.

Produces a single JSON file capturing row counts, content checksums, and
sample data from every source the downloader currently uses:

    DB tables    : pids, downloaded, pending_pids, pending_urls
    JSON files   : exist_pid.json, pixiv_cookie_requirement.json
    Text files   : pictures_id.txt, all_url.txt, err_url.txt
    Filesystem   : download path + legacy_scan_paths (PID + page extraction)

The output lives at ``%APPDATA%/pixiv_download/history/state_dump_<ts>.json``
and serves as the ground truth that ``tools/verify_consistency.py`` later
compares the new ``artworks``+``pages`` schema against.

This module is intentionally read-only — it never modifies any source.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from app.core.pid_filesystem import scan_paths


def appdata_root() -> str:
    return os.path.join(os.environ["APPDATA"], "pixiv_download")


def read_settings() -> dict:
    with open(os.path.join(appdata_root(), "settings.json"), encoding="utf-8") as f:
        return json.load(f)


def sha256_hex(items) -> str:
    """Order-independent checksum of a string iterable."""
    h = hashlib.sha256()
    for s in sorted(str(x) for x in items):
        h.update(s.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def dump_db(db_path: str) -> dict:
    """Read all relevant tables from metadata.sqlite3 without writing."""
    out: dict = {}
    if not os.path.isfile(db_path):
        return {"present": False}
    cx = sqlite3.connect(db_path)
    cur = cx.cursor()

    cur.execute("SELECT pid FROM pids")
    pid_set = {r[0] for r in cur.fetchall()}
    out["pids"] = {"count": len(pid_set), "checksum": sha256_hex(pid_set)}

    cur.execute("SELECT pid FROM downloaded")
    dl = {r[0] for r in cur.fetchall()}
    out["downloaded"] = {"count": len(dl), "checksum": sha256_hex(dl)}

    cur.execute("SELECT pid FROM pending_pids WHERE status='pending'")
    pp = {r[0] for r in cur.fetchall()}
    out["pending_pids"] = {"count": len(pp), "checksum": sha256_hex(pp)}

    cur.execute("SELECT url FROM pending_urls WHERE status='pending'")
    pu = {r[0] for r in cur.fetchall()}
    out["pending_urls"] = {"count": len(pu), "checksum": sha256_hex(pu)}

    # Schema fingerprint so we know which tables existed when this snapshot ran
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    out["schema_tables"] = [r[0] for r in cur.fetchall()]

    cx.close()
    return out


def dump_json_pid_set(path: str) -> dict:
    """Read a JSON file whose top level is a list or dict-keyed PID set."""
    if not os.path.isfile(path):
        return {"present": False}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        pid_set = {str(k) for k in data.keys()}
    elif isinstance(data, list):
        pid_set = {str(x) for x in data}
    else:
        return {"present": True, "error": f"unexpected_type:{type(data).__name__}"}
    return {
        "present": True,
        "count": len(pid_set),
        "checksum": sha256_hex(pid_set),
        "size_bytes": os.path.getsize(path),
    }


def dump_text_lines(path: str) -> dict:
    """Read a text file as a set of unique non-empty lines."""
    if not os.path.isfile(path):
        return {"present": False}
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = {ln.strip() for ln in f if ln.strip()}
    return {
        "present": True,
        "count": len(lines),
        "checksum": sha256_hex(lines),
        "size_bytes": os.path.getsize(path),
    }


def scan_filesystem(roots: list[str]) -> dict:
    """Wrap ``pid_filesystem.scan_paths`` with the JSON shape this script
    persists. Adds a checksum field so two snapshots can be compared
    without re-loading the full PID set.
    """
    result = scan_paths(roots).to_jsonable()
    result["pid_set_checksum"] = sha256_hex(result["pid_anyfile"])
    return result


def main() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    root = appdata_root()
    out_dir = os.path.join(root, "history")
    os.makedirs(out_dir, exist_ok=True)

    settings = read_settings()
    dl_path = settings.get("download", {}).get("path") or ""
    legacy = settings.get("download", {}).get("legacy_scan_paths") or []
    if isinstance(legacy, str):
        legacy = [legacy]
    scan_roots = [dl_path] + list(legacy)
    # Also include the parent of the legacy directory if it's a tier folder —
    # the user's F:\pixiv has many sub-tiers (10000+/, 3000+/, etc.) that
    # legacy_scan_paths only covers partially.
    parent_candidates: list[str] = []
    for p in scan_roots:
        parent = os.path.dirname(p.rstrip("/\\"))
        if parent and os.path.isdir(parent) and parent not in scan_roots:
            parent_candidates.append(parent)
    if parent_candidates:
        scan_roots = list(dict.fromkeys(scan_roots + parent_candidates))

    print(f"=== dump_state @ {ts} ===")
    print(f"scan roots: {scan_roots}")

    state: dict = {
        "timestamp": ts,
        "appdata_root": root,
        "settings_download_path": dl_path,
        "scan_roots": scan_roots,
        "db": dump_db(os.path.join(root, "metadata.sqlite3")),
        "files": {
            "exist_pid.json": dump_json_pid_set(os.path.join(root, "exist_pid.json")),
            "pixiv_cookie_requirement.json": dump_json_pid_set(
                os.path.join(root, "pixiv_cookie_requirement.json")
            ),
            "pictures_id.txt": dump_text_lines(os.path.join(root, "pictures_id.txt")),
            "all_url.txt": dump_text_lines(os.path.join(root, "all_url.txt")),
            "err_url.txt": dump_text_lines(os.path.join(root, "err_url.txt")),
        },
        "filesystem": scan_filesystem(scan_roots),
    }

    # Pretty-print the headline numbers
    print("\n=== DB ===")
    for k, v in state["db"].items():
        if isinstance(v, dict) and "count" in v:
            print(f"  {k}: {v['count']:,}  sha256={v['checksum'][:12]}")
        else:
            print(f"  {k}: {v}")
    print("\n=== Files ===")
    for k, v in state["files"].items():
        if v.get("present"):
            print(f"  {k}: {v['count']:,}  sha256={v['checksum'][:12]}  ({v['size_bytes']:,} B)")
        else:
            print(f"  {k}: (not present)")
    print("\n=== Filesystem ===")
    fs = state["filesystem"]
    for root_path, info in fs["per_root"].items():
        if info.get("present"):
            print(
                f"  {root_path}: files={info['files_total']:,} "
                f"pid_matches={info['files_with_pid']:,} distinct_pids={info['distinct_pids']:,}"
            )
        else:
            print(f"  {root_path}: (not present)")
    print(f"  total_files={fs['total_files']:,}")
    print(f"  distinct_pids_with_files={fs['distinct_pids_with_files']:,}")
    print(f"  distinct_pids_with_page_index={fs['distinct_pids_with_page_index']:,}")

    # Write the full snapshot. Pretty=False for compactness — this file is
    # for machine consumption (diff against later snapshots).
    out_path = os.path.join(out_dir, f"state_dump_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    print(f"\n→ Wrote {os.path.getsize(out_path):,} B to {out_path}")
    return out_path


if __name__ == "__main__":
    main()
