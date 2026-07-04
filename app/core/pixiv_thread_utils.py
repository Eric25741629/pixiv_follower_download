import datetime
import json
import os
import shutil
import sys
import traceback

# Atomic write / backup primitives live in safe_io.py — re-exported here so
# callers can continue importing them from pixiv_thread_utils.
from app.core.safe_io import (  # noqa: F401  (public re-export)
    atomic_write_text,
    atomic_write_json,
    atomic_append_text,
    backup_file,
)


def safe_json(res, *keys, default=None):
    """Walk res.json()[k1][k2]... defensively.

    Returns ``default`` if the response is not JSON, the API signalled an
    error envelope (``{"error": true, ...}``), or any key is missing.
    Pixiv ajax endpoints return ``{"error": true, "message": "..."}`` (no
    ``body``) on cookie expiry / rate-limit; without this guard the caller
    crashes with ``KeyError: 'body'``.
    """
    try:
        data = res.json()
    except Exception:
        return default
    if isinstance(data, dict) and data.get("error"):
        return default
    cur = data
    for key in keys:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def output_err(e):
    error_class = e.__class__.__name__
    detail = e.args[0] if e.args else ""
    _, _, tb = sys.exc_info()
    if tb is None:
        return f"[{error_class}] {detail}"
    lastCallStack = traceback.extract_tb(tb)[-1]
    fileName = lastCallStack[0]
    lineNum = lastCallStack[1]
    funcName = lastCallStack[2]
    errMsg = f"File \"{fileName}\", line {lineNum}, in {funcName}: [{error_class}] {detail}"
    return errMsg


# PID normalization + filename mining moved to pid_utils.py (file-size refactor).
# Re-exported so existing ``from app.core.pixiv_thread_utils import normalize_pid``
# (and normalize_pid_set / canonicalize_pximg_url_for_storage / the filename
# miner) callers keep working unchanged.
from app.core.pid_utils import (  # noqa: F401  (public re-export)
    normalize_pid,
    normalize_pid_set,
    canonicalize_pximg_url_for_storage,
    _extract_pid_candidates_from_name,
    _PID_FROM_NAME_PATTERNS,
)


def trash_file(file_path, base_path, max_days=30):
    """移到 base_path/trash/YYYYMMDD_HHMMSS_filename，並清除超過 max_days 天的舊垃圾。"""
    trash_dir = os.path.join(base_path, "trash")
    try:
        os.makedirs(trash_dir, exist_ok=True)
        now = datetime.datetime.now()
        for fname in os.listdir(trash_dir):
            fpath = os.path.join(trash_dir, fname)
            try:
                age = (now - datetime.datetime.fromtimestamp(os.path.getmtime(fpath))).days
                if age >= max_days:
                    os.remove(fpath)
            except Exception:
                pass
    except Exception:
        pass
    if not os.path.isfile(file_path):
        return
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(trash_dir, f"{ts}_{os.path.basename(file_path)}" )
    try:
        shutil.move(file_path, dst)
    except Exception:
        pass


def _parse_exist_pid_list(data):
    """Convert raw JSON list to set, preserving p1/p2 page-tracking suffixes (only strip p0)."""
    if not isinstance(data, list):
        return set()
    return set(str(x).replace("p0", "") for x in data if str(x).strip())


def _augment_exist_pid_from_db(base_path, json_set):
    """Union the JSON-derived set with the SQLite downloaded set, if available.

    This catches PIDs that a previous worker marked as downloaded in the
    DB but failed to flush to ``exist_pid.json`` (e.g. crashed mid-run).
    Returns ``json_set`` unchanged when no DB exists, when SQLite import
    fails, or when the DB is empty.
    """
    db_file = os.path.join(base_path, "metadata.sqlite3")
    if not os.path.isfile(db_file):
        return json_set
    try:
        from app.core.metadata_db import MetadataDB
        db_set = MetadataDB(base_path).downloaded_set()
    except Exception:
        return json_set
    if not db_set:
        return json_set
    return json_set | db_set


def _read_exist_pid_json(json_path):
    """Read the canonical exist_pid.json; returns the parsed set or None on miss."""
    if not os.path.isfile(json_path):
        return None
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return _parse_exist_pid_list(data)


def _read_legacy_exist_pid_set(legacy_json_path, txt_path):
    """Read the legacy exist.json or existPID.txt formats. Returns set (possibly empty)."""
    if os.path.isfile(legacy_json_path):
        with open(legacy_json_path, encoding="utf-8") as f:
            data = json.load(f)
        return _parse_exist_pid_list(data)
    if os.path.isfile(txt_path):
        with open(txt_path, encoding="utf-8") as f:
            return set(line.rstrip().replace("p0", "") for line in f if line.rstrip())
    return set()


def _trash_legacy_exist_pid_files(base_path, legacy_paths):
    """Move any legacy exist_pid file variants to trash/."""
    for old in legacy_paths:
        if os.path.isfile(old):
            trash_file(old, base_path)


def load_exist_pid_set(base_path):
    """Load exist_pid from the canonical JSON file, merged with the SQLite cache.

    PHASE-A: this whole function (and its helpers ``_read_exist_pid_json``,
    ``_read_legacy_exist_pid_set``, ``_trash_legacy_exist_pid_files``,
    ``_augment_exist_pid_from_db``) is slated for removal in Phase B. The
    Phase B replacement at call sites is::

        MetadataDB(base_path).closed_artwork_set()

    Step 4 already uses that directly (``thread_download._load_initial_exist_pid_set``);
    Step 2/3 still go through here for compatibility while the disk scan
    shadow-writes both stores.

    Automatically migrates legacy formats (exist.json / existPID.txt) to
    exist_pid.json on first run and moves old files to trash/. When a
    sibling ``metadata.sqlite3`` is present, its ``downloaded`` set is
    unioned into the result so a JSON file lagging behind the DB doesn't
    cause re-download.
    """
    if not base_path:
        return set()
    json_path = os.path.join(base_path, "exist_pid.json")
    legacy_json_path = os.path.join(base_path, "exist.json")
    txt_path = os.path.join(base_path, "existPID.txt")
    legacy_paths = [legacy_json_path, txt_path]
    try:
        primary = _read_exist_pid_json(json_path)
        if primary is not None:
            out = primary
            _trash_legacy_exist_pid_files(base_path, legacy_paths)
        else:
            out = _read_legacy_exist_pid_set(legacy_json_path, txt_path)
            if out:
                atomic_write_json(json_path, list(out), backup=False)
                _trash_legacy_exist_pid_files(base_path, legacy_paths)
        out = _augment_exist_pid_from_db(base_path, out)
    except Exception:
        out = set()
    return out


# Download-folder scan + mtime-signature cache moved to folder_scan.py
# (file-size refactor). Re-exported below so existing callers and the
# ``sync_exist_pid_with_download_folder`` orchestrator (which stays here and
# looks these up as module globals) are unchanged.
from app.core.folder_scan import (  # noqa: F401  (public re-export)
    _folder_dir_mtimes_match,
    _get_folder_file_count_cache_path,
    _load_folder_file_count_cache,
    _save_folder_file_count_cache,
    _scan_download_folder,
    scan_download_folder_for_pid_set,
)


# ─── PHASE-A-EXIST-PID-MIGRATION ──────────────────────────────────────────
# Plan (recorded so the cutover can be done in one focused PR later):
#
#   Phase A (current): JSON is still the canonical store; DB shadow-writes
#                      keep metadata.sqlite3 in sync so externally-dropped
#                      PIDs land in both places. Reads still go through
#                      ``load_exist_pid_set`` (JSON ∪ DB).
#   Phase B (future) : Switch all reads to ``db.closed_artwork_set()``,
#                      delete the JSON write/read paths + the legacy
#                      ``exist.json`` / ``existPID.txt`` fallbacks. Hunt
#                      for "PHASE-A" comments to find every line that
#                      should disappear or simplify.
#
# Every line in this module that's only kept for the JSON path is tagged
# ``# PHASE-A`` — search for that token to enumerate the removal surface.
# ──────────────────────────────────────────────────────────────────────────


def _shadow_write_exist_pid_to_db(base_path, scanned_pids, *, event_log=None):
    """PHASE-A: mirror disk-scanned PIDs into metadata.sqlite3.

    Best-effort sync so Phase B (DB-only reads) can be enabled by just
    deleting the JSON write line — the DB will already have the data.
    Lazily creates the DB file on first call; ``MetadataDB._conn()``
    runs the schema bootstrap when the sqlite file is opened for the
    first time. We must NOT gate on ``os.path.isfile(db_file)`` — a
    fresh profile that runs Step 4 directly after pointing at an
    existing download folder would silently lose every scanned PID
    (step 4 reads ``closed_artwork_set()`` from the DB built moments
    later by ``download_thread.__init__``).
    """
    base = str(base_path or "").strip()
    if not base or not scanned_pids:
        return
    try:
        from app.core.metadata_db import MetadataDB, mirror_exist_pid_set
    except Exception:
        return
    db = None
    try:
        db = MetadataDB(base, event_log=event_log)
        mirror_exist_pid_set(db, scanned_pids)
    except Exception:
        pass
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def sync_exist_pid_with_download_folder(base_path, download_path, current_exist_pid=None, recursive=True, *, event_log=None):
    """
    同步 exist_pid 與下載資料夾，使用檔案數量快取來避免重複掃描。
    只有在資料夾檔案數量變化時才會重新掃描。

    PHASE-A: writes scanned PIDs to BOTH exist_pid.json (canonical for now)
    AND metadata.sqlite3 (shadow). Phase B will drop the JSON branch.
    """
    disk_set = load_exist_pid_set(base_path)  # PHASE-A: JSON+DB union
    merged = set(disk_set)
    merged.update(normalize_pid_set(current_exist_pid))

    # 目錄 mtime 簽章快取：未變動就完全略過 os.walk（只 stat 已知目錄）
    cache = _load_folder_file_count_cache(base_path)
    download_path_norm = os.path.normpath(str(download_path or ""))
    cached_info = cache.get(download_path_norm, {})
    cached_dir_mtimes = cached_info.get("dir_mtimes", {})
    prev_cached_pids = set(cached_info.get("pids", []))

    used_cache = _folder_dir_mtimes_match(cached_dir_mtimes)
    new_pids = set()
    if used_cache:
        scanned_pids = prev_cached_pids
        scanned_files = int(cached_info.get("file_count", 0) or 0)
    else:
        # 任一目錄 mtime 變了（或首次/舊格式快取）→ 重新掃描一趟
        scanned_pids, dir_mtimes, scanned_files = _scan_download_folder(
            download_path, recursive=recursive,
        )
        new_pids = scanned_pids - prev_cached_pids
        cache[download_path_norm] = {
            "file_count": scanned_files,
            "dir_mtimes": dir_mtimes,
            "pids": list(scanned_pids),
            "updated_at": datetime.datetime.now().isoformat(),
        }
        _save_folder_file_count_cache(base_path, cache)

    before_scan = set(merged)
    merged.update(scanned_pids)
    added_from_download = len(merged - before_scan)
    changed_vs_disk = merged != disk_set

    if base_path and changed_vs_disk:
        json_path = os.path.join(base_path, "exist_pid.json")
        atomic_write_json(json_path, list(merged), backup=True)  # PHASE-A
    # PHASE-A: shadow-write only the *newly discovered* PIDs (the delta).
    # On a cache hit nothing is new; previously-scanned PIDs were already
    # mirrored on the run that discovered them. This avoids re-importing the
    # whole folder set (200k+ rows) every run AND keeps the DB file signature
    # stable so the closed-artwork-set cache survives across the step build.
    if new_pids:
        _shadow_write_exist_pid_to_db(base_path, new_pids, event_log=event_log)

    return {
        "merged_set": merged,
        "scanned_files": scanned_files,
        "scanned_pid_count": len(scanned_pids),
        "added_from_download": added_from_download,
        "changed_vs_disk": changed_vs_disk,
        "used_cache": used_cache,
    }


def invalidate_folder_file_count_cache(base_path, download_path=None):
    """
    使檔案數量快取失效。
    如果指定了 download_path，只清除該路徑的快取；
    否則清除所有快取。
    """
    try:
        cache_path = _get_folder_file_count_cache_path(base_path)
        if not os.path.isfile(cache_path):
            return

        if download_path is None:
            # 清除所有快取
            atomic_write_json(cache_path, {}, backup=True)
        else:
            # 清除特定路徑的快取
            cache = _load_folder_file_count_cache(base_path)
            download_path_norm = os.path.normpath(str(download_path))
            if download_path_norm in cache:
                del cache[download_path_norm]
                _save_folder_file_count_cache(base_path, cache)
    except Exception:
        pass


def append_diagnostic_event(base_path, event, **fields):
    """
    Append one JSON-line diagnostic event under user data folder.
    """
    try:
        root = str(base_path or "").strip()
        if not root:
            root = os.path.join(os.getenv('APPDATA') or "", 'pixiv_download')
        os.makedirs(root, exist_ok=True)
        log_path = os.path.join(root, "all_url_diagnostics.jsonl")
        payload = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "event": str(event),
        }
        if fields:
            payload.update(fields)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return log_path
    except Exception:
        return None


def _normalize_pid_lines(line_iter):
    """Iterate ``line_iter``, yielding the normalized non-empty PIDs."""
    out = []
    for line in line_iter:
        text = str(line).strip()
        if not text:
            continue
        pid = normalize_pid(text)
        if pid:
            out.append(pid)
    return out


def read_pid_lines(file_path):
    """Read a PID text file, returning normalized non-empty PID strings.

    Handles missing files and UTF-8 decode errors (falls back to errors='ignore').
    Returns an empty list (not a generator) so callers can iterate multiple times.
    """
    if not os.path.isfile(file_path):
        return []
    try:
        with open(file_path, encoding='utf-8') as f:
            lines = f.readlines()
        return _normalize_pid_lines(lines)
    except UnicodeDecodeError:
        pass
    except Exception:
        return []
    try:
        with open(file_path, encoding='utf-8', errors='ignore') as f:
            return _normalize_pid_lines(f)
    except Exception:
        return []


def safe_meta_count(db) -> int:
    """`db.meta_count()` with a 0 fallback (db may be None or broken)."""
    try:
        return int(db.meta_count())
    except Exception:
        return 0


# JSON read + history-backup recovery helpers moved to json_recovery.py
# (file-size refactor). Re-exported so existing
# ``from app.core.pixiv_thread_utils import safe_read_json`` /
# ``read_json_with_recovery`` (and the internal _safe_emit / _NO_RECOVERY /
# _try_recover_from_history / _list_history_backups / _atomic_restore_file
# names) callers keep working unchanged.
from app.core.json_recovery import (  # noqa: F401  (public re-export)
    _NO_RECOVERY,
    _atomic_restore_file,
    _list_history_backups,
    _safe_emit,
    _try_recover_from_history,
    read_json_with_recovery,
    safe_read_json,
)


# Cookie pool parsing / dedupe / aliasing / usage-label moved to cookie_utils.py
# (file-size refactor). Re-exported so existing
# ``from app.core.pixiv_thread_utils import normalize_cookie_entries`` (and the
# rest of the cookie helpers) callers keep working unchanged.
from app.core.cookie_utils import (  # noqa: F401  (public re-export)
    _strip_cookie_prefix,
    _parse_cookie_entry,
    _merge_duplicate_entry,
    _dedupe_cookie_entries,
    _fill_missing_aliases,
    normalize_cookie_entries,
    normalize_cookie_pool,
    cookie_usage_label,
    format_cookie_usage_summary,
    init_cookie_fields,
)


def fetch_with_cookie_retry(http_get, url, headers, cookies, retry_statuses=(403, 404),
                            timeout=(10, 30)):
    """
    Request once, then retry once with Cookie when status is in retry_statuses
    and the first request didn't include Cookie.
    Returns (final_response, trace_info, first_response_or_none).

    ``timeout`` is a (connect, read) tuple passed to ``http_get``. It MUST be
    set: this is the ugoira_meta fetch that precedes every ugoira download, and
    an unbounded request here can hang the worker forever (the 2026-06-21 wedge,
    same class as the image-body hang). The body is read non-streamed by the
    caller (``json.loads(resp.content)``), so the read timeout bounds a silent
    socket while the small JSON makes a trickle window negligible.
    """
    first_headers = dict(headers or {})
    first_response = http_get(url, headers=first_headers, stream=True, timeout=timeout)
    first_status = getattr(first_response, "status_code", None)
    trace = {
        "first_try_status": first_status,
        "retry_used": False,
        "retry_with_cookie_status": None,
        "final_status": first_status,
    }
    has_cookie_in_first = bool(first_headers.get("Cookie"))
    if first_status in set(retry_statuses or ()) and (not has_cookie_in_first) and str(cookies or "").strip():
        retry_headers = dict(first_headers)
        retry_headers["Cookie"] = str(cookies).strip()
        retry_response = http_get(url, headers=retry_headers, stream=True, timeout=timeout)
        retry_status = getattr(retry_response, "status_code", None)
        trace["retry_used"] = True
        trace["retry_with_cookie_status"] = retry_status
        trace["final_status"] = retry_status
        return retry_response, trace, first_response
    return first_response, trace, None


def count_text_lines(file_path):
    """Count non-blank lines in a UTF-8 text file; returns 0 on any error."""
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            return sum(1 for line in f if str(line).strip())
    except Exception:
        return 0


def to_int_lenient(value, default=None):
    """Best-effort int coercion: strips commas/underscores, accepts numeric
    strings via float→int.  Returns ``default`` on any failure."""
    try:
        if isinstance(value, str):
            s = value.strip().replace(",", "").replace("_", "")
            if not s:
                return default
            return int(float(s))
        return int(value)
    except Exception:
        return default


def normalize_filter_tags(tags):
    """Lowercase + strip + dedupe a tag-filter list.  Returns ``[]`` for
    non-list input.  Operates on the raw tag string with no alias / translation
    rewriting, so the user-configured filter strings are compared verbatim
    against Pixiv's source tags.

    .. warning::
        Historical behavior routed filter tags through ``tag_edit.Tag()``
        which applied ~300 Japanese→Chinese translation rules. That module
        was removed because the silent rewriting (a) broke filter matching
        when users typed the Japanese form, and (b) permanently mutated the
        cached tag strings in ``artworks.tags`` and ``all_url_meta.json``.

        **Migration note for callers:** if a user's ban_tag / must_tag list
        was tuned against the *translated* string (e.g. ``Hololive`` instead
        of ``ホロライブ``), those entries silently stop matching after the
        switch to verbatim comparison. The user must re-enter the filter
        tags using Pixiv's original strings.
    """
    out = []
    if not isinstance(tags, list):
        return out
    for t in tags:
        s = str(t).strip()
        if s:
            out.append(s.lower())
    return list(dict.fromkeys(out))


def mirror_meta_dict_to_db(db, url_meta):
    """Bulk-import ``url_meta`` into the SQLite metadata cache via
    ``db.import_meta_dict``.  No-op when ``db`` is None or ``url_meta`` is
    not a non-empty dict.  Silently swallows exceptions (best-effort sync)."""
    if db is None:
        return
    try:
        if isinstance(url_meta, dict) and url_meta:
            db.import_meta_dict(url_meta)
    except Exception:
        pass


def write_all_url_file(path, urls, *, reason, diag):
    """Write ``urls`` to ``path/all_url.txt`` with atomic-write + raw-write
    fallback, emitting diag events at each stage.

    Returns True on success.  ``diag`` is invoked as ``diag(event, **fields)``
    — callers usually pass the worker's bound ``_diag`` method.
    """
    file_path = os.path.join(path, "all_url.txt")
    safe_lines = [str(x) for x in (urls or [])]
    before_count = count_text_lines(file_path)
    diag(
        "all_url_write_attempt",
        reason=reason,
        before_count=before_count,
        target_count=len(safe_lines),
    )
    try:
        atomic_write_text(file_path, safe_lines, backup=True)
        diag(
            "all_url_write_done",
            reason=reason,
            success=True,
            before_count=before_count,
            after_count=count_text_lines(file_path),
        )
        return True
    except Exception as err:
        diag("all_url_write_primary_failed", reason=reason, error=str(err))
    try:
        backup_file(file_path)
    except Exception:
        pass
    try:
        with open(file_path, "w+", encoding="utf-8") as f:
            f.writelines([line + "\n" for line in safe_lines])
        diag(
            "all_url_write_done",
            reason=reason,
            success=True,
            via="fallback",
            before_count=before_count,
            after_count=count_text_lines(file_path),
        )
        return True
    except Exception as fallback_err:
        diag(
            "all_url_write_done",
            reason=reason,
            success=False,
            error=str(fallback_err),
        )
        return False
