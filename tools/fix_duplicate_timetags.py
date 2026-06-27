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
_PID_RE = re.compile(r"PID(\d+)")
_FMT = "%Y%m%d_%H%M%S"


def _parse_tag(tag: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(tag, _FMT)
    except ValueError:
        return None


def _pid_of(dirpath: str, name: str) -> str:
    """Group key for a file: its PID when present, else a per-file key.

    All pages of one PID share a timetag by design, so the dedup unit is the
    PID — not the individual file. Files with no parseable PID fall back to a
    unique per-file key (legacy per-file de-duplication for those)."""
    m = _PID_RE.search(name)
    return f"PID:{m.group(1)}" if m else f"FILE:{dirpath}\0{name}"


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
    """Compute [(dirpath, old_name, new_name, new_tag)] giving each PID one tag.

    De-duplication unit is the PID, not the file: all pages of one PID share a
    timetag by design and must stay together. Each PID's original tag = the min
    tag among its pages. PIDs are processed in (original-tag, pid) order; the
    first PID claiming a tag keeps it, a later PID colliding on it advances +1 s
    to the next unused tag and ALL its pages move there together. The set of
    originally-occupied tags is pre-seeded so an existing PID's tag is never
    stolen (no cascade), matching the prior single-page behaviour.
    """
    by_pid: dict[str, list] = {}
    for dirpath, name, tag in entries:
        by_pid.setdefault(_pid_of(dirpath, name), []).append((dirpath, name, tag))
    pid_orig = {pid: min(t for _d, _n, t in files) for pid, files in by_pid.items()}

    used = set(pid_orig.values())      # every PID's original tag is occupied
    taken: set[str] = set()            # tags already claimed in this pass
    assigned: dict[str, str] = {}
    for pid in sorted(by_pid, key=lambda p: (pid_orig[p], p)):
        tag = pid_orig[pid]
        if tag not in taken:
            taken.add(tag)
            assigned[pid] = tag
            continue
        dt = _parse_tag(tag)
        while True:
            dt += datetime.timedelta(seconds=1)
            new_tag = dt.strftime(_FMT)
            if new_tag not in used and new_tag not in taken:
                break
        used.add(new_tag)
        taken.add(new_tag)
        assigned[pid] = new_tag

    renames = []
    for pid, files in by_pid.items():
        new_tag = assigned[pid]
        for dirpath, name, tag in files:
            if tag != new_tag:
                renames.append((dirpath, name, new_tag + name[len(tag):], new_tag))
    renames.sort(key=lambda r: (r[0], r[1]))
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
