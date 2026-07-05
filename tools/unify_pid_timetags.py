"""Unify within-PID filename timetag prefixes (no cross-PID de-dupe).

Every page of one PID shares ONE timetag by design; pages that got a different
``YYYYMMDD_HHMMSS`` prefix (legacy per-page +1 s files, or a page retried by a
later run before the pid_timetags.json sidecar existed) are renamed to the
PID's EARLIEST prefix, and their atime/mtime aligned to it. Files whose target
name already exists (true duplicates) are skipped and reported.

Unlike ``fix_duplicate_timetags.py`` this deliberately does NOT reassign
cross-PID tag collisions — it only restores the within-PID invariant.

Dry-run by default; ``--apply`` renames and appends an undo log (JSON lines
``{"dir", "old", "new"}``) so every rename is reversible.

Usage:
    python tools/unify_pid_timetags.py --root "E:/pixiv" [--apply] [--undo-log PATH]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import re
import sys

_PREFIX_RE = re.compile(r"^(\d{8}_\d{6})_(.+)$")
_PID_RE = re.compile(r"PID(\d+)")
_FMT = "%Y%m%d_%H%M%S"


def _valid_tag(tag: str) -> bool:
    try:
        datetime.datetime.strptime(tag, _FMT)
        return True
    except ValueError:
        return False


def collect_by_pid(root: str):
    """{pid: [(dirpath, name, tag)]} for every timetag-prefixed PID file."""
    by_pid: dict[str, list] = {}
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            m = _PREFIX_RE.match(name)
            if not m or not _valid_tag(m.group(1)):
                continue
            p = _PID_RE.search(name)
            if not p:
                continue
            total += 1
            by_pid.setdefault(p.group(1), []).append((dirpath, name, m.group(1)))
    return by_pid, total


def plan_renames(by_pid):
    """[(dirpath, old_name, new_name, target_tag)] unifying each PID to its min tag."""
    renames = []
    split_pids = 0
    for entries in by_pid.values():
        tags = {t for _d, _n, t in entries}
        if len(tags) <= 1:
            continue
        split_pids += 1
        target = min(tags)
        for dirpath, name, tag in entries:
            if tag != target:
                renames.append((dirpath, name, target + name[len(tag):], target))
    renames.sort()
    return renames, split_pids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="unify_pid_timetags")
    ap.add_argument("--root", required=True, help="下載資料夾根目錄")
    ap.add_argument("--apply", action="store_true", help="實際改名（預設只列出計畫）")
    ap.add_argument("--undo-log", default="unify_pid_timetags_undo.jsonl",
                    help="改名紀錄（JSON lines，可逆向還原）")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"root 不存在: {args.root}", file=sys.stderr)
        return 1

    by_pid, total = collect_by_pid(args.root)
    renames, split_pids = plan_renames(by_pid)
    print(f"掃描 {total} 個時間戳檔名 / {len(by_pid)} 個 PID，"
          f"前綴不一致 PID {split_pids} 個，需改名 {len(renames)} 個", file=sys.stderr)

    renamed = skipped = 0
    with contextlib.ExitStack() as stack:
        undo = (stack.enter_context(open(args.undo_log, "a", encoding="utf-8"))
                if args.apply else None)
        for dirpath, old, new, tag in renames:
            src = os.path.join(dirpath, old)
            dst = os.path.join(dirpath, new)
            if os.path.exists(dst):
                print(f"略過(目標已存在): {dst}", file=sys.stderr)
                skipped += 1
                continue
            if args.apply:
                os.rename(src, dst)
                ts = datetime.datetime.strptime(tag, _FMT).timestamp()
                os.utime(dst, (ts, ts))
                undo.write(json.dumps({"dir": dirpath, "old": old, "new": new},
                                      ensure_ascii=False) + "\n")
                renamed += 1
            else:
                print(f"[dry-run] {old} -> {new}", file=sys.stderr)

    mode = "已套用" if args.apply else "dry-run（加 --apply 實際執行）"
    print(f"完成（{mode}）：改名 {renamed}、略過 {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
