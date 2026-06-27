"""Download-folder scan + mtime-signature cache (file-size refactor).

Split out of ``pixiv_thread_utils.py``. The single-pass ``os.walk`` PID scan,
its ``(pids, file_count)`` compatibility wrapper, the O(#dirs) mtime-signature
check that lets a later run skip the walk entirely, and the file-count cache
load/save helpers. ``pixiv_thread_utils`` re-imports everything here so
existing callers (and the ``sync_exist_pid_with_download_folder`` orchestrator
that stays in ``pixiv_thread_utils`` and looks these up as module globals — the
test monkeypatches ``ptu._scan_download_folder``) are unchanged.

Imports ``safe_read_json`` from ``json_recovery`` (not ``pixiv_thread_utils``)
to avoid an import cycle.
"""
from __future__ import annotations

import os

from app.core.json_recovery import safe_read_json
from app.core.pid_utils import _extract_pid_candidates_from_name
from app.core.safe_io import atomic_write_json


def _scan_download_folder(download_path, recursive=True):
    """Single ``os.walk`` pass returning ``(pids, dir_mtimes, file_count)``.

    ``dir_mtimes`` maps every visited directory to its ``st_mtime_ns``. It is
    the cheap change signature a later run uses (via
    :func:`_folder_dir_mtimes_match`) to skip the walk entirely: adding,
    removing or renaming any file updates its parent directory's mtime, and
    creating a sub-directory updates *its* parent's mtime, so re-stat'ing the
    known directory list detects every change in O(#dirs) stat calls instead
    of re-enumerating every file.
    """
    found = set()
    dir_mtimes = {}
    file_count = 0
    p = str(download_path or "").strip()
    if not p or (not os.path.isdir(p)):
        return found, dir_mtimes, file_count
    for root, _, files in os.walk(p):
        try:
            dir_mtimes[root] = os.stat(root).st_mtime_ns
        except OSError:
            pass
        for file_name in files:
            file_count += 1
            try:
                found.update(_extract_pid_candidates_from_name(file_name))
            except Exception:
                pass
        if not recursive:
            break
    return found, dir_mtimes, file_count


def scan_download_folder_for_pid_set(download_path, recursive=True):
    """Backward-compatible wrapper: ``(pids, file_count)`` via one walk."""
    found, _dir_mtimes, scanned_files = _scan_download_folder(
        download_path, recursive=recursive,
    )
    return found, scanned_files


def _folder_dir_mtimes_match(cached_dir_mtimes):
    """True iff every cached directory still exists with the same mtime_ns.

    O(#dirs) ``os.stat`` calls, no file enumeration. A missing/changed
    directory (file added/removed/renamed anywhere, or a sub-dir created)
    fails the check and forces a full rescan. Empty/old-format cache → False.
    """
    if not isinstance(cached_dir_mtimes, dict) or not cached_dir_mtimes:
        return False
    for dir_path, mtime_ns in cached_dir_mtimes.items():
        try:
            if os.stat(dir_path).st_mtime_ns != int(mtime_ns):
                return False
        except (OSError, TypeError, ValueError):
            return False
    return True


def _get_folder_file_count_cache_path(base_path):
    """取得檔案數量快取檔案的路徑"""
    return os.path.join(base_path, "folder_file_count_cache.json")


def _load_folder_file_count_cache(base_path):
    """載入檔案數量快取"""
    cache_path = _get_folder_file_count_cache_path(base_path)
    return safe_read_json(cache_path, {})


def _save_folder_file_count_cache(base_path, cache):
    """儲存檔案數量快取"""
    try:
        cache_path = _get_folder_file_count_cache_path(base_path)
        atomic_write_json(cache_path, cache, backup=True)
    except Exception:
        pass
