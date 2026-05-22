"""Filename → (PID, page_index) extraction + recursive disk scan.

Single source of truth for parsing PIDs out of filenames. Used by:

    tools/dump_state.py     — ground-truth snapshot generator
    tools/db_migration.py   — backfill of pages.status='downloaded'
    thread_download.splitID — currently a duplicate, slated for replacement

Three filename forms are recognised:

    _PID12345p0…ext      Current canonical format (no underscore before p).
    PID=12345_p0.ext     Older form some external tools emit.
    12345_p0.ext         Legacy / URL-derived form before the rename.

For ugoira (animated) the marker is ``ugoira<N>`` instead of ``p<N>``.

The page-index extraction is *separate* from PID-only extraction so that
single-page files lacking an explicit page suffix (e.g. ugoira gifs named
``…PID12345.gif``) can still be detected as "PID has at least one file."
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

# Capture (pid, page_index) when the page indicator is present.
_PID_PAGE_LITERAL = re.compile(r"PID=?(\d{4,12})(?:_)?(?:p|ugoira)(\d+)")
_PID_PAGE_UNDER = re.compile(r"(?:^|[^0-9])(\d{6,12})_(?:p|ugoira)(\d+)")
# Capture PID alone when no page indicator is present.
_PID_ONLY = re.compile(r"PID=?(\d{4,12})")

# (pid, page_index) from a pximg URL path tail.
# Matches both ``_p<N>`` and ``_ugoira<N>``. The canonical template form
# ``..._p.<ext>`` (page-less) is treated as page 0 by ``parse_url``.
_URL_PAGE = re.compile(
    r"/(\d{4,12})(?:-[a-f0-9]+)?_(?:p|ugoira)(\d+)\.[A-Za-z0-9]+(?:[?#]|$)"
)
_URL_TEMPLATE = re.compile(
    r"/(\d{4,12})(?:-[a-f0-9]+)?_(?:p|ugoira)\.[A-Za-z0-9]+"
)
# In img_url templates returned by Pixiv_info the page indicator is
# ``_p`` or ``_p<N>`` immediately before the extension. ``build_page_url``
# below rewrites either form to ``_p<N>``.
_URL_PAGE_REWRITE = re.compile(r"_(p|ugoira)\d*(?=\.[A-Za-z0-9]+$)")


def extract_pid_pages(filename: str) -> list[tuple[str, int]]:
    """Return all (pid, page_index) tuples found in *filename*.

    Empty list when no page-indexed pattern matches. A single filename can
    legitimately yield multiple tuples (unusual, but cheap to handle).
    """
    out: list[tuple[str, int]] = []
    for m in _PID_PAGE_LITERAL.finditer(filename):
        out.append((m.group(1), int(m.group(2))))
    if out:
        return out
    for m in _PID_PAGE_UNDER.finditer(filename):
        out.append((m.group(1), int(m.group(2))))
    return out


def extract_pid_only(filename: str) -> str | None:
    """Return the PID from *filename* even when no page suffix is present.

    Useful for "at least one file exists for this PID" detection on
    single-file artworks (ugoira gif without an explicit ``ugoira0``).
    """
    m = _PID_ONLY.search(filename)
    return m.group(1) if m else None


def parse_pid_and_page_from_url(url: str) -> tuple[str | None, int | None]:
    """Extract ``(pid, page_index)`` from a pximg URL.

    Returns ``(None, None)`` if neither pattern matches. ``..._p.<ext>``
    (canonical template form Pixiv_info returns) is treated as page 0
    because that is the only page index addressable from a templated URL.
    """
    s = url or ""
    m = _URL_PAGE.search(s)
    if m:
        return m.group(1), int(m.group(2))
    m = _URL_TEMPLATE.search(s)
    if m:
        return m.group(1), 0
    return None, None


def build_page_url(img_url_template: str | None, page_index: int) -> str | None:
    """Rewrite a Pixiv ``img_url`` template into the URL for *page_index*.

    Accepts both the canonical ``..._p.<ext>`` form (no page) and the
    legacy ``..._p<N>.<ext>`` form returned by older Pixiv responses; in
    either case the result is ``..._p<page_index>.<ext>``. Returns
    ``None`` when the template doesn't contain a recognisable page slot.
    """
    s = (img_url_template or "").strip()
    if not s:
        return None
    rewritten, n_subs = _URL_PAGE_REWRITE.subn(rf"_\g<1>{int(page_index)}", s)
    return rewritten if n_subs > 0 else None


class DiskScanResult:
    """Lightweight container for ``scan_paths`` output.

    Attributes:
        pid_pages      — {pid: {page_index, ...}} of page-indexed matches.
        pid_anyfile    — set of PIDs detected via ANY pattern (incl. PID-only).
        pid_first_path — {pid: first matching absolute path} for debugging /
                         backfill of pages.file_path.
        per_root       — {root: {files_total, files_with_pid, distinct_pids}}.
        total_files    — total file count across all roots.
    """

    __slots__ = ("pid_pages", "pid_anyfile", "pid_first_path",
                 "per_root", "total_files")

    def __init__(self):
        self.pid_pages: defaultdict[str, set[int]] = defaultdict(set)
        self.pid_anyfile: set[str] = set()
        self.pid_first_path: dict[str, str] = {}
        self.per_root: dict[str, dict] = {}
        self.total_files: int = 0

    def to_jsonable(self) -> dict:
        return {
            "pid_pages": {p: sorted(v) for p, v in self.pid_pages.items()},
            "pid_anyfile": sorted(self.pid_anyfile),
            "pid_first_path": self.pid_first_path,
            "per_root": self.per_root,
            "total_files": self.total_files,
            "distinct_pids_with_files": len(self.pid_anyfile),
            "distinct_pids_with_page_index": len(self.pid_pages),
        }


def _ingest_file(
    full_path: str,
    filename: str,
    result: "DiskScanResult",
    local_pids: set[str],
) -> bool:
    """Process one filename into the running scan result. Returns True if
    any PID was extracted from the filename (used for the per-root counters).
    """
    pairs = extract_pid_pages(filename)
    if pairs:
        for pid, page in pairs:
            result.pid_pages[pid].add(page)
            result.pid_anyfile.add(pid)
            result.pid_first_path.setdefault(pid, full_path)
            local_pids.add(pid)
        return True
    pid_only = extract_pid_only(filename)
    if pid_only:
        result.pid_anyfile.add(pid_only)
        result.pid_first_path.setdefault(pid_only, full_path)
        local_pids.add(pid_only)
        return True
    return False


def _walk_files(root: str):
    """Yield ``(dirpath, filename)`` for every file under *root*. One
    intermediate generator keeps the caller's iteration depth at 1."""
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            yield dirpath, fn


def _scan_one_root(root: str, result: "DiskScanResult") -> dict:
    """Scan a single root, updating *result* in-place. Returns the per-root
    summary that goes into result.per_root."""
    n_files = 0
    n_match = 0
    local_pids: set[str] = set()
    for dirpath, fn in _walk_files(root):
        n_files += 1
        result.total_files += 1
        if _ingest_file(os.path.join(dirpath, fn), fn, result, local_pids):
            n_match += 1
    return {
        "present": True,
        "files_total": n_files,
        "files_with_pid": n_match,
        "distinct_pids": len(local_pids),
    }


def scan_paths(roots: list[str]) -> DiskScanResult:
    """Walk every directory in *roots* and extract PID+page info from
    filenames. Missing or non-directory roots are recorded but skipped.
    """
    result = DiskScanResult()
    for root in roots:
        if not root or not os.path.isdir(root):
            result.per_root[root or "(empty)"] = {"present": False}
            continue
        result.per_root[root] = _scan_one_root(root, result)
    return result
