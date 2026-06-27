"""Repair duplicate filename timetag prefixes left by the missing
"timechanged" persistence bug (mass files starting with the same
``YYYYMMDD_HHMMSS_`` prefix).

For every file under ``--root`` whose name matches ``YYYYMMDD_HHMMSS_<rest>``,
duplicated prefixes are re-stamped: the first file of each prefix keeps it,
each subsequent duplicate gets the next *unused* second (globally unique
across the whole tree, like the downloader's +1 s counter). The file is
renamed and its atime/mtime set to the new timetag. With ``--sync-mtime``
the kept (non-renamed) files also get their mtime aligned to their prefix.

Dry-run by default; pass ``--apply`` to actually rename.

Usage:
    python tools/fix_duplicate_timetags.py --root "D:/Pixiv_download" [--apply] [--sync-mtime]

Note: ``pages.file_path`` in metadata.sqlite3 is informational only (workflow
decisions key on ``status``), so renames do not affect resume/skip logic.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

_PREFIX_RE = re.compile(r"^(\d{8}_\d{6})_(.+)$")
_FMT = "%Y%m%d_%H%M%S"


def _parse_tag(tag: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(tag, _FMT)
    except ValueError:
        return None


def collect(root: str):
    """Return [(dirpath, filename, tag)] for every timetag-prefixed file."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            m = _PREFIX_RE.match(name)
            if m and _parse_tag(m.group(1)) is not None:
                out.append((dirpath, name, m.group(1)))
    return out


def plan_renames(entries):
    """Compute [(dirpath, old_name, new_name, new_tag)] de-duplicating prefixes.

    Deterministic: entries sorted by (tag, path); the first occurrence of a
    tag keeps it, later ones advance +1 s to the next globally unused tag.
    """
    entries = sorted(entries, key=lambda e: (e[2], e[0], e[1]))
    used = {e[2] for e in entries}
    seen: set[str] = set()
    renames = []
    for dirpath, name, tag in entries:
        if tag not in seen:
            seen.add(tag)
            continue
        dt = _parse_tag(tag)
        while True:
            dt += datetime.timedelta(seconds=1)
            new_tag = dt.strftime(_FMT)
            if new_tag not in used:
                break
        used.add(new_tag)
        seen.add(new_tag)
        new_name = new_tag + name[len(tag):]
        renames.append((dirpath, name, new_name, new_tag))
    return renames


def apply_utime(path: str, tag: str) -> None:
    dt = _parse_tag(tag)
    if dt is None:
        return
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fix_duplicate_timetags")
    ap.add_argument("--root", required=True, help="下載資料夾根目錄")
    ap.add_argument("--apply", action="store_true", help="實際改名（預設只列出計畫）")
    ap.add_argument("--sync-mtime", action="store_true",
                    help="同時把所有(含未改名)檔案的 mtime 對齊其檔名時間戳")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"root 不存在: {args.root}", file=sys.stderr)
        return 1

    entries = collect(args.root)
    renames = plan_renames(entries)
    print(f"掃描 {len(entries)} 個時間戳檔名，需改名 {len(renames)} 個", file=sys.stderr)

    renamed = skipped = 0
    for dirpath, old, new, tag in renames:
        src = os.path.join(dirpath, old)
        dst = os.path.join(dirpath, new)
        if os.path.exists(dst):
            print(f"略過(目標已存在): {dst}", file=sys.stderr)
            skipped += 1
            continue
        if args.apply:
            os.rename(src, dst)
            apply_utime(dst, tag)
            renamed += 1
        else:
            print(f"[dry-run] {src} -> {new}", file=sys.stderr)

    if args.sync_mtime and args.apply:
        renamed_old = {(d, o) for d, o, _n, _t in renames}
        for dirpath, name, tag in entries:
            if (dirpath, name) in renamed_old:
                continue  # renamed files were stamped at rename time
            apply_utime(os.path.join(dirpath, name), tag)

    mode = "已套用" if args.apply else "dry-run（加 --apply 實際執行）"
    print(f"完成（{mode}）：改名 {renamed}、略過 {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
