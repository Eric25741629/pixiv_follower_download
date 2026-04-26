import datetime
import json
import os
import re
import shutil
import sys
import traceback


def output_err(e):
    error_class = e.__class__.__name__
    detail = e.args[0] if e.args else ""
    cl, exc, tb = sys.exc_info()
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


def load_exist_pid_set(base_path):
    """Load exist_pid from the single canonical JSON file.

    Automatically migrates legacy formats (exist.json / existPID.txt) to
    exist_pid.json on first run and moves old files to trash/.
    """
    out = set()
    if not base_path:
        return out
    json_path = os.path.join(base_path, "exist_pid.json")
    legacy_json_path = os.path.join(base_path, "exist.json")
    txt_path = os.path.join(base_path, "existPID.txt")
    try:
        if os.path.isfile(json_path):
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            out = _parse_exist_pid_list(data)
            # Opportunistically trash lingering legacy files
            for old in [legacy_json_path, txt_path]:
                if os.path.isfile(old):
                    trash_file(old, base_path)
            return out
        # Legacy migration: read whichever format exists, write exist_pid.json, trash old
        if os.path.isfile(legacy_json_path):
            with open(legacy_json_path, encoding="utf-8") as f:
                data = json.load(f)
            out = _parse_exist_pid_list(data)
        elif os.path.isfile(txt_path):
            with open(txt_path, encoding="utf-8") as f:
                out = set(line.rstrip().replace("p0", "") for line in f if line.rstrip())
        if out:
            atomic_write_json(json_path, list(out), backup=False)
            for old in [legacy_json_path, txt_path]:
                if os.path.isfile(old):
                    trash_file(old, base_path)
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
        used_cache = True
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
        used_cache = False

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


def backup_file(file_path, max_history=10):
    try:
        if not os.path.exists(file_path):
            return
        hist = _ensure_history_dir(file_path)
        if not hist:
            return
        ts = datetime.datetime.now().strftime('%Y%m%d')
        base = os.path.basename(file_path)
        dst = os.path.join(hist, f"{base}.{ts}")
        if os.path.exists(dst):
            idx = 1
            while True:
                candidate = os.path.join(hist, f"{base}.{ts}.{idx}")
                if not os.path.exists(candidate):
                    dst = candidate
                    break
                idx += 1
        shutil.copy2(file_path, dst)
        try:
            files = [f for f in os.listdir(hist) if f.startswith(base + '.')]
            files.sort(key=lambda x: os.path.getmtime(os.path.join(hist, x)), reverse=True)
            while len(files) > max_history:
                old_file = os.path.join(hist, files.pop())
                os.remove(old_file)
        except Exception:
            pass
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


def read_pid_lines(file_path):
    """Read a PID text file, yielding normalized non-empty PID strings.

    Handles missing files, UTF-8 decode errors, and strips 'p0' prefix via normalize_pid.
    Returns an empty list (not a generator) so callers can iterate multiple times.
    """
    out = []
    try:
        if not os.path.isfile(file_path):
            return out
        try:
            opener = open(file_path, encoding='utf-8')
        except Exception:
            return out
        with opener as f:
            try:
                lines = f.readlines()
            except UnicodeDecodeError:
                pass
            else:
                for line in lines:
                    text = str(line).strip()
                    if not text:
                        continue
                    pid = normalize_pid(text)
                    if pid:
                        out.append(pid)
                return out
        # fallback: re-open with errors='ignore'
        with open(file_path, encoding='utf-8', errors='ignore') as f:
            for line in f:
                text = str(line).strip()
                if not text:
                    continue
                pid = normalize_pid(text)
                if pid:
                    out.append(pid)
    except Exception:
        pass
    return out


def safe_read_json(file_path, default=None):
    """Read a JSON file, returning `default` on any error (missing file, decode error, etc.)."""
    try:
        if not os.path.isfile(file_path):
            return default
        with open(file_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def normalize_cookie_entries(raw_value, alias_map=None):
    """Normalise any raw cookie input into a deduplicated list of {cookie, alias} dicts.

    alias_map: optional {cookie_str: alias_str} dict to fill in aliases that are not
    already embedded in the raw entries (used by the GUI cookie-persistence layer).
    """
    entries = []
    if isinstance(raw_value, (list, tuple, set)):
        candidates = list(raw_value)
    else:
        candidates = [raw_value]
    for item in candidates:
        alias = ""
        if isinstance(item, dict):
            text = str(item.get("cookie", "") or "").strip()
            alias = str(item.get("alias", "") or "").strip()
        else:
            text = str(item or "").strip()
        if not text:
            continue
        if text.lower().startswith("cookie:"):
            text = text.split(":", 1)[1].strip()
        if text:
            entries.append({"cookie": text, "alias": alias})
    deduped = []
    seen = {}
    for item in entries:
        cookie_text = str(item.get("cookie", "") or "").strip()
        alias_text = str(item.get("alias", "") or "").strip()
        if not cookie_text:
            continue
        if cookie_text in seen:
            idx = seen[cookie_text]
            if alias_text and not str(deduped[idx].get("alias", "")).strip():
                deduped[idx]["alias"] = alias_text
            continue
        seen[cookie_text] = len(deduped)
        deduped.append({"cookie": cookie_text, "alias": alias_text})
    if isinstance(alias_map, dict) and alias_map:
        for entry in deduped:
            if not entry.get("alias"):
                entry["alias"] = str(alias_map.get(entry.get("cookie", ""), "") or "").strip()
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


def cookie_speed_divisor(cookie_pool):
    """Speed multiplier for multi-cookie pool: n=1→1.0x, n=2→1.6x … max 4.0x."""
    try:
        n = len(cookie_pool or [])
    except Exception:
        n = 0
    if n <= 1:
        return 1.0
    return min(4.0, 1.0 + 0.6 * float(n - 1))


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
