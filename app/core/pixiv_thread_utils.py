import datetime
import json
import os
import re
import shutil
import sys
import traceback


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


def normalize_pid(value):
    s = str(value).strip()
    if not s:
        return ""
    if '_' in s:
        s = s.split('_', 1)[0]
    s = s.replace('p0', '')
    m = re.search(r"\d+", s)
    if m:
        return m.group(0)
    return s


def normalize_pid_set(values):
    out = set()
    if not values:
        return out
    try:
        for v in values:
            pid = normalize_pid(v)
            if pid:
                out.add(pid)
    except Exception:
        pid = normalize_pid(values)
        if pid:
            out.add(pid)
    return out


def canonicalize_pximg_url_for_storage(url):
    """
    Normalize pximg original URL to a stable storage form without hash segment.
    Example:
      .../139112835-c476..._p0.jpg -> .../139112835_p0.jpg
    """
    try:
        s = str(url).strip()
        if not s:
            return s
        head, tail = s.rsplit("/", 1)
        # Keep only the PID and page marker in filename for all_url.txt readability.
        tail2 = re.sub(
            r"^(\d{5,12})-[a-f0-9]+(_(?:p\d+|ugoira\d+)\.[A-Za-z0-9]+)$",
            r"\1\2",
            tail,
            flags=re.IGNORECASE,
        )
        return head + "/" + tail2
    except Exception:
        return str(url)


_PID_FROM_NAME_PATTERNS = [
    re.compile(r"PID[=\s_-]?(\d{5,12})", re.IGNORECASE),
    re.compile(r"illust[_-]?(\d{5,12})", re.IGNORECASE),
    re.compile(r"(\d{5,12})_p\d+", re.IGNORECASE),
    re.compile(r"(\d{5,12})p\d+", re.IGNORECASE),
    # pximg original filename: 139112835-<hash>_p0.jpg
    re.compile(r"^(\d{5,12})-[a-f0-9]+_p\d+", re.IGNORECASE),
]


def _extract_pid_candidates_from_name(file_name):
    out = set()
    try:
        name = os.path.basename(str(file_name))
    except Exception:
        return out
    for pattern in _PID_FROM_NAME_PATTERNS:
        try:
            for pid in pattern.findall(name):
                n = normalize_pid(pid)
                if n:
                    out.add(n)
        except Exception:
            pass
    try:
        stem, _ = os.path.splitext(name)
        if re.fullmatch(r"\d{5,12}", stem):
            out.add(stem)
    except Exception:
        pass
    return out


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


def scan_download_folder_for_pid_set(download_path, recursive=True):
    found = set()
    scanned_files = 0
    p = str(download_path or "").strip()
    if not p or (not os.path.isdir(p)):
        return found, scanned_files
    for root, _, files in os.walk(p):
        for file_name in files:
            scanned_files += 1
            try:
                found.update(_extract_pid_candidates_from_name(file_name))
            except Exception:
                pass
        if not recursive:
            break
    return found, scanned_files


def _get_folder_file_count_cache_path(base_path):
    """取得檔案數量快取檔案的路徑"""
    return os.path.join(base_path, "folder_file_count_cache.json")


def _count_files_in_folder(download_path, recursive=True):
    """遞迴計算資料夾中的檔案數量"""
    p = str(download_path or "").strip()
    if not p or (not os.path.isdir(p)):
        return 0
    count = 0
    for root, _, files in os.walk(p):
        count += len(files)
        if not recursive:
            break
    return count


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


def sync_exist_pid_with_download_folder(base_path, download_path, current_exist_pid=None, recursive=True):
    """
    同步 exist_pid 與下載資料夾，使用檔案數量快取來避免重複掃描。
    只有在資料夾檔案數量變化時才會重新掃描。
    """
    disk_set = load_exist_pid_set(base_path)
    merged = set(disk_set)
    merged.update(normalize_pid_set(current_exist_pid))

    # 檢查檔案數量快取
    cache = _load_folder_file_count_cache(base_path)
    current_file_count = _count_files_in_folder(download_path, recursive=recursive)

    download_path_norm = os.path.normpath(str(download_path or ""))
    cached_info = cache.get(download_path_norm, {})
    cached_count = cached_info.get("file_count", -1)
    cached_pids = set(cached_info.get("pids", []))

    # 如果檔案數量相同，使用快取的 PID 結果
    if cached_count == current_file_count and current_file_count > 0:
        scanned_pids = cached_pids
        scanned_files = current_file_count
    else:
        # 檔案數量變化，重新掃描
        before_scan = set(merged)
        scanned_pids, scanned_files = scan_download_folder_for_pid_set(download_path, recursive=recursive)
        merged.update(scanned_pids)
        added_from_download = len(merged - before_scan)
        changed_vs_disk = merged != disk_set

        # 更新快取
        cache[download_path_norm] = {
            "file_count": current_file_count,
            "pids": list(scanned_pids),
            "updated_at": datetime.datetime.now().isoformat()
        }
        _save_folder_file_count_cache(base_path, cache)

        if base_path and changed_vs_disk:
            json_path = os.path.join(base_path, "exist_pid.json")
            atomic_write_json(json_path, list(merged), backup=True)

        return {
            "merged_set": merged,
            "scanned_files": scanned_files,
            "scanned_pid_count": len(scanned_pids),
            "added_from_download": added_from_download,
            "changed_vs_disk": changed_vs_disk,
            "used_cache": False,
        }

    # 使用快取的情況
    before_scan = set(merged)
    merged.update(scanned_pids)
    added_from_download = len(merged - before_scan)
    changed_vs_disk = merged != disk_set

    if base_path and changed_vs_disk:
        json_path = os.path.join(base_path, "exist_pid.json")
        atomic_write_json(json_path, list(merged), backup=True)

    return {
        "merged_set": merged,
        "scanned_files": scanned_files,
        "scanned_pid_count": len(scanned_pids),
        "added_from_download": added_from_download,
        "changed_vs_disk": changed_vs_disk,
        "used_cache": True,
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


def _ensure_history_dir(file_path):
    try:
        d = os.path.dirname(file_path) or os.getcwd()
        hist = os.path.join(d, 'history')
        os.makedirs(hist, exist_ok=True)
        return hist
    except Exception:
        return None


def _next_unique_history_path(hist, base, ts):
    """Return a backup path that doesn't exist yet (appends .1, .2, ... on collision)."""
    dst = os.path.join(hist, f"{base}.{ts}")
    if not os.path.exists(dst):
        return dst
    idx = 1
    while True:
        candidate = os.path.join(hist, f"{base}.{ts}.{idx}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def _prune_history_backups(hist, base, max_history):
    """Keep at most ``max_history`` files in ``hist`` whose name starts with ``base.``."""
    try:
        files = [f for f in os.listdir(hist) if f.startswith(base + '.')]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(hist, x)), reverse=True)
        while len(files) > max_history:
            os.remove(os.path.join(hist, files.pop()))
    except Exception:
        pass


def backup_file(file_path, max_history=10):
    try:
        if not os.path.exists(file_path):
            return
        hist = _ensure_history_dir(file_path)
        if not hist:
            return
        base = os.path.basename(file_path)
        ts = datetime.datetime.now().strftime('%Y%m%d')
        dst = _next_unique_history_path(hist, base, ts)
        shutil.copy2(file_path, dst)
        _prune_history_backups(hist, base, max_history)
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


def atomic_write_text(file_path, lines, encoding='utf-8', backup=True):
    try:
        if backup:
            try:
                backup_file(file_path)
            except Exception:
                pass
        tmp = file_path + '.tmp'
        dirp = os.path.dirname(file_path)
        if dirp:
            os.makedirs(dirp, exist_ok=True)
        with open(tmp, 'w', encoding=encoding) as f:
            if isinstance(lines, (list, tuple)):
                f.writelines([str(x) + '\n' for x in lines])
            else:
                f.write(str(lines))
        os.replace(tmp, file_path)
    except Exception:
        try:
            with open(file_path, 'w', encoding=encoding) as f:
                if isinstance(lines, (list, tuple)):
                    f.writelines([str(x) + '\n' for x in lines])
                else:
                    f.write(str(lines))
        except Exception:
            pass


def atomic_write_json(file_path, obj, encoding='utf-8', backup=True):
    try:
        if backup:
            try:
                backup_file(file_path)
            except Exception:
                pass
        tmp = file_path + '.tmp'
        dirp = os.path.dirname(file_path)
        if dirp:
            os.makedirs(dirp, exist_ok=True)
        with open(tmp, 'w', encoding=encoding) as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, file_path)
    except Exception:
        try:
            with open(file_path, 'w', encoding=encoding) as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


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


def safe_read_json(file_path, default=None):
    """Read a JSON file, returning `default` on any error (missing file, decode error, etc.)."""
    try:
        if not os.path.isfile(file_path):
            return default
        with open(file_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _safe_emit(emit, html):
    """Best-effort callback into the optional ``emit`` hook."""
    if emit is None:
        return
    try:
        emit(html)
    except Exception:
        pass


def _list_history_backups(hist_dir, base):
    """Return sibling backup files for ``base``, newest first."""
    try:
        candidates = [
            os.path.join(hist_dir, n) for n in os.listdir(hist_dir)
            if n.startswith(base + '.')
        ]
    except Exception:
        return []
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates


def _atomic_restore_file(file_path, value):
    """Replace a corrupt JSON file with ``value`` via tmp + os.replace."""
    try:
        tmp = file_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.replace(tmp, file_path)
    except Exception:
        pass


_NO_RECOVERY = object()


def _try_recover_from_history(file_path, emit):
    """Attempt to restore a corrupt JSON file from its history/ backups.

    Returns the recovered value on success, or the sentinel
    ``_NO_RECOVERY`` when no usable backup was found (so callers can
    distinguish a legitimately recovered ``None`` from "nothing found").
    """
    hist_dir = os.path.join(os.path.dirname(file_path), 'history')
    base = os.path.basename(file_path)
    if not os.path.isdir(hist_dir):
        _safe_emit(emit,
            f"<p><font color='red'>[警告] 無 history/ 備份可還原，"
            f"{base} 將以空值繼續。</font></p>"
        )
        return _NO_RECOVERY
    for cand in _list_history_backups(hist_dir, base):
        try:
            with open(cand, encoding='utf-8') as f:
                value = json.load(f)
        except Exception:
            continue
        _atomic_restore_file(file_path, value)
        n = len(value) if isinstance(value, (dict, list)) else 0
        _safe_emit(emit,
            f"<p><font color='green'>[還原] 已從 "
            f"history/{os.path.basename(cand)} 還原 {n} 筆</font></p>"
        )
        return value
    _safe_emit(emit,
        f"<p><font color='red'>[警告] history/ 內所有 {base} 備份"
        f"都無法解析，將以空值繼續。</font></p>"
    )
    return _NO_RECOVERY


def read_json_with_recovery(file_path, default=None, emit=None):
    """Read a JSON file; on parse failure, try to auto-recover from
    the latest valid backup in ``history/`` next to it.

    Returns ``(value, status)`` where status is one of:
      'missing'   — file doesn't exist; returned default
      'ok'        — file parsed cleanly
      'recovered' — file was corrupt; restored from history/<name>.<...>
      'corrupt'   — file was corrupt and no usable backup found

    ``emit`` is an optional callback ``emit(html_message)`` for surfacing
    recovery actions to the user (e.g., the worker thread's _q.put).
    """
    if not os.path.isfile(file_path):
        return default, 'missing'
    try:
        with open(file_path, encoding='utf-8') as f:
            return json.load(f), 'ok'
    except Exception as parse_err:
        _safe_emit(emit,
            f"<p><font color='red'>[警告] {os.path.basename(file_path)} "
            f"解析失敗（{type(parse_err).__name__}），嘗試從 history/ 還原...</font></p>"
        )
        recovered = _try_recover_from_history(file_path, emit)
        if recovered is _NO_RECOVERY:
            return default, 'corrupt'
        return recovered, 'recovered'


def _strip_cookie_prefix(text):
    """Drop the leading ``Cookie:`` prefix some users paste verbatim."""
    if text.lower().startswith("cookie:"):
        return text.split(":", 1)[1].strip()
    return text


def _parse_cookie_entry(item):
    """Convert one raw input (str or dict) into a normalised entry dict.

    Returns ``None`` for empty / blank inputs so the caller can skip them.
    """
    if isinstance(item, dict):
        text = str(item.get("cookie", "") or "").strip()
        alias = str(item.get("alias", "") or "").strip()
        status = str(item.get("status", "") or "").strip()
        last_tested_at = item.get("last_tested_at", None)
    else:
        text = str(item or "").strip()
        alias = ""
        status = ""
        last_tested_at = None

    if not text:
        return None
    text = _strip_cookie_prefix(text)
    if not text:
        return None

    entry = {"cookie": text, "alias": alias}
    if status:
        entry["status"] = status
    if last_tested_at is not None:
        entry["last_tested_at"] = last_tested_at
    return entry


def _merge_duplicate_entry(existing, duplicate):
    """Carry alias / status / last_tested_at forward when the dedupe target is missing them."""
    alias_text = str(duplicate.get("alias", "") or "").strip()
    if alias_text and not str(existing.get("alias", "")).strip():
        existing["alias"] = alias_text
    if "status" in duplicate and not existing.get("status"):
        existing["status"] = duplicate["status"]
    if "last_tested_at" in duplicate and existing.get("last_tested_at") is None:
        existing["last_tested_at"] = duplicate["last_tested_at"]


def _dedupe_cookie_entries(entries):
    """Collapse entries with the same cookie string, merging metadata first-wins."""
    deduped = []
    seen = {}
    for item in entries:
        cookie_text = str(item.get("cookie", "") or "").strip()
        if not cookie_text:
            continue
        if cookie_text in seen:
            _merge_duplicate_entry(deduped[seen[cookie_text]], item)
            continue
        seen[cookie_text] = len(deduped)
        new_entry = {
            "cookie": cookie_text,
            "alias": str(item.get("alias", "") or "").strip(),
        }
        if "status" in item:
            new_entry["status"] = item["status"]
        if "last_tested_at" in item:
            new_entry["last_tested_at"] = item["last_tested_at"]
        deduped.append(new_entry)
    return deduped


def _fill_missing_aliases(entries, alias_map):
    """Populate empty ``alias`` fields from a ``{cookie: alias}`` lookup."""
    if not isinstance(alias_map, dict) or not alias_map:
        return
    for entry in entries:
        if entry.get("alias"):
            continue
        entry["alias"] = str(alias_map.get(entry.get("cookie", ""), "") or "").strip()


def normalize_cookie_entries(raw_value, alias_map=None):
    """Normalise any raw cookie input into a deduplicated list of {cookie, alias} dicts.

    alias_map: optional {cookie_str: alias_str} dict to fill in aliases that are not
    already embedded in the raw entries (used by the GUI cookie-persistence layer).
    """
    if isinstance(raw_value, (list, tuple, set)):
        candidates = list(raw_value)
    else:
        candidates = [raw_value]

    parsed = [e for e in (_parse_cookie_entry(item) for item in candidates) if e]
    deduped = _dedupe_cookie_entries(parsed)
    _fill_missing_aliases(deduped, alias_map)
    return deduped


def normalize_cookie_pool(raw_value):
    """Return a deduplicated list of cookie strings from raw input."""
    return [x.get("cookie", "") for x in normalize_cookie_entries(raw_value) if str(x.get("cookie", "")).strip()]


def cookie_usage_label(cookie_value, cookie_pool=None, alias_map=None):
    """Return a human-readable label for a cookie value (alias → pool index → fallback)."""
    cookie_text = str(cookie_value or "").strip()
    if not cookie_text:
        return "未提供Cookie"
    try:
        if isinstance(alias_map, dict):
            alias = str(alias_map.get(cookie_text, "") or "").strip()
            if alias:
                return alias
    except Exception:
        pass
    try:
        if cookie_pool and cookie_text in cookie_pool:
            return f"Cookie{cookie_pool.index(cookie_text) + 1}"
    except Exception:
        pass
    return "Cookie"


def format_cookie_usage_summary(cookie_usage_counts, cookie_pool=None, alias_map=None):
    """Return a summary string of cookie usage counts."""
    try:
        if not isinstance(cookie_usage_counts, dict) or not cookie_usage_counts:
            return "未使用 Cookie"
        normalized_items = []
        total = 0
        for cookie_label, count in cookie_usage_counts.items():
            try:
                count_int = int(count)
            except Exception:
                count_int = 0
            if count_int <= 0:
                continue
            total += count_int
            normalized_items.append((str(cookie_label), count_int))
        if total <= 0:
            return "未使用 Cookie"
        normalized_items.sort(key=lambda item: (-item[1], item[0]))
        parts = [f"{lbl} {cnt} 次" for lbl, cnt in normalized_items]
        return "總計 {} 次；{}".format(total, "，".join(parts))
    except Exception:
        return "未使用 Cookie"


# Deprecated: superseded by AccountScheduler per-account cooldown. Kept for import compat.
def cookie_speed_divisor(cookie_pool):
    """Speed multiplier for multi-cookie pool: n=1→1.0x, n=2→1.6x … max 4.0x."""
    try:
        n = len(cookie_pool or [])
    except Exception:
        n = 0
    if n <= 1:
        return 1.0
    return min(4.0, 1.0 + 0.6 * float(n - 1))


# Deprecated: superseded by AccountScheduler per-account cooldown. Kept for import compat.
def apply_cookie_pool_speedup(delay, cookie_pool):
    """Reduce delay proportionally to cookie pool size."""
    try:
        d = int(delay)
    except Exception:
        return delay
    if d <= 0:
        return 0
    div = cookie_speed_divisor(cookie_pool)
    if div <= 1.0:
        return d
    return max(1, int(round(float(d) / div)))


def init_cookie_fields(raw_cookies):
    """
    Parse raw cookie input into the 4-tuple used by thread __init__.
    Returns (cookie_entries, cookie_pool, alias_map, first_cookie_str).
    """
    entries = normalize_cookie_entries(raw_cookies)
    pool = [x.get("cookie", "") for x in entries if str(x.get("cookie", "")).strip()]
    alias_map = {
        str(x.get("cookie", "")).strip(): str(x.get("alias", "") or "").strip()
        for x in entries
        if str(x.get("cookie", "")).strip()
    }
    first = pool[0] if pool else str(raw_cookies or "").strip()
    return entries, pool, alias_map, first


def fetch_with_cookie_retry(http_get, url, headers, cookies, retry_statuses=(403, 404)):
    """
    Request once, then retry once with Cookie when status is in retry_statuses
    and the first request didn't include Cookie.
    Returns (final_response, trace_info, first_response_or_none).
    """
    first_headers = dict(headers or {})
    first_response = http_get(url, headers=first_headers, stream=True)
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
        retry_response = http_get(url, headers=retry_headers, stream=True)
        retry_status = getattr(retry_response, "status_code", None)
        trace["retry_used"] = True
        trace["retry_with_cookie_status"] = retry_status
        trace["final_status"] = retry_status
        return retry_response, trace, first_response
    return first_response, trace, None
