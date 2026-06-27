"""Cookie pool parsing, dedupe, aliasing and usage-label helpers.

Extracted verbatim from ``pixiv_thread_utils`` (file-size refactor). These are
pure, stdlib-only helpers with zero coupling to the rest of the module, used by
the worker threads' __init__, the GUI cookie-persistence layer, and the cookies
view. ``cookie_speed_divisor`` / ``apply_cookie_pool_speedup`` are deprecated
(superseded by AccountScheduler) and kept only for import compatibility.

``pixiv_thread_utils`` re-exports every name here so existing
``from app.core.pixiv_thread_utils import normalize_cookie_entries`` callers keep
working.
"""


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
        enabled_raw = item.get("enabled", None)
    else:
        text = str(item or "").strip()
        alias = ""
        status = ""
        last_tested_at = None
        enabled_raw = None

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
    # Only persist `enabled` when explicitly disabled; missing key means
    # enabled (the default), so legacy entries stay free of the key.
    if enabled_raw is False:
        entry["enabled"] = False
    return entry


def _merge_duplicate_entry(existing, duplicate):
    """Carry alias / status / last_tested_at / enabled forward when the dedupe target is missing them."""
    alias_text = str(duplicate.get("alias", "") or "").strip()
    if alias_text and not str(existing.get("alias", "")).strip():
        existing["alias"] = alias_text
    if "status" in duplicate and not existing.get("status"):
        existing["status"] = duplicate["status"]
    if "last_tested_at" in duplicate and existing.get("last_tested_at") is None:
        existing["last_tested_at"] = duplicate["last_tested_at"]
    # Explicit `enabled=False` on either side wins (safer default: keep disabled).
    if duplicate.get("enabled") is False or existing.get("enabled") is False:
        existing["enabled"] = False


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
        if item.get("enabled") is False:
            new_entry["enabled"] = False
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
