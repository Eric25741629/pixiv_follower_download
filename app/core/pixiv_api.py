import json
import os
import random
import time

import bs4
import requests

from app.core.proxy_utils import to_requests_proxies


def make_session(proxy_url: "str | None" = None) -> requests.Session:
    """Create a requests.Session pre-configured with proxy and SSL settings.

    The caller is expected to pass a URL that has been validated by
    ``proxy_utils.parse_proxy_url`` (or ``None`` for direct connection).
    """
    sess = requests.Session()
    proxies = to_requests_proxies(proxy_url)
    if proxies:
        sess.proxies.update(proxies)
    sess.verify = True
    return sess


# 子執行緒的工作函數
import re
import threading

from app.core.pixiv_thread_utils import safe_read_json


# ── pixiv_cookie_requirement.json process cache ───────────────────────────────
# ``get_pixiv_cookie_requirement`` is called per-page on the combined-mode
# download leg (``thread_download._resolve_pid_and_cookie``) whenever a PID's
# cached meta has no ``requires_cookie`` value, and can repeat inside the JPEG
# retry loop. Each call previously ``safe_read_json``'d the ENTIRE file. This
# process-global cache mirrors the ``closed_artwork_set`` pattern in
# ``metadata_db_closed_set.py``: it keys on a cheap (size, mtime_ns) file
# signature and re-reads/parses only when the signature changes. A missing
# file maps to an empty dict, and the signature distinguishes
# deletion/creation/modification so the cache invalidates automatically with
# no manual reset to get wrong.
_COOKIE_REQUIREMENT_CACHE: "dict[str, tuple]" = {}
_COOKIE_REQUIREMENT_CACHE_LOCK = threading.RLock()


def _cookie_requirement_file_signature(path):
    """Cheap change signature: (size, mtime_ns) of the JSON file.

    One ``os.stat`` call, no file read. ``None`` for a missing file so a
    not-yet-created and a freshly-deleted file compare distinctly.
    """
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _load_cookie_requirement_map(path):
    """Return the parsed ``pixiv_cookie_requirement.json`` dict, process-cached.

    Re-reads + parses only when the file signature changes; otherwise serves
    the previously parsed dict. The returned dict is shared (read-only by
    callers); a non-dict / missing file yields an empty dict.
    """
    key = os.path.normcase(os.path.normpath(path))
    sig = _cookie_requirement_file_signature(path)
    with _COOKIE_REQUIREMENT_CACHE_LOCK:
        cached = _COOKIE_REQUIREMENT_CACHE.get(key)
        if cached is not None and cached[0] == sig:
            return cached[1]
    data = safe_read_json(path, None)
    if not isinstance(data, dict):
        data = {}
    with _COOKIE_REQUIREMENT_CACHE_LOCK:
        _COOKIE_REQUIREMENT_CACHE[key] = (sig, data)
    return data


def _extract_artwork_body(payload):
    """從 Pixiv ajax 回傳的 payload 中萃取 body dict。

    payload['body'] 可能是 dict、list 或缺失；統一回傳 dict（最差情況為空 dict）。
    """
    if not isinstance(payload, dict):
        return {}
    body = payload.get('body', {})
    if isinstance(body, list):
        body = body[0] if (len(body) > 0 and isinstance(body[0], dict)) else {}
    if not isinstance(body, dict):
        body = {}
    return body


def _ai_type_label(body):
    """aiType==2 時回傳 'AI生成'，否則回傳 None。"""
    ai_type = body.get('aiType', None)
    if ai_type is None:
        return None
    try:
        if int(ai_type) == 2:
            return 'AI生成'
    except (TypeError, ValueError):
        return None
    return None


def _normalize_raw_tags_field(body):
    """把 body['tags'] 統一成 list；可能來源是 list、含 'tags' 子鍵的 dict、或單一值。"""
    raw_tags = body.get('tags', [])
    if isinstance(raw_tags, dict):
        raw_tags = raw_tags.get('tags', [])
    if isinstance(raw_tags, list):
        return raw_tags
    return [raw_tags] if raw_tags else []


def _tag_entry_to_str(entry):
    """把單一 tag entry 轉為字串；entry 可能是 str、dict（多種命名）或其他原值。"""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        tag_name = entry.get('tag') or entry.get('name') or entry.get('translated_name')
        if not tag_name and isinstance(entry.get('translation'), dict):
            tag_name = entry['translation'].get('en')
        return str(tag_name) if tag_name else None
    if entry is not None:
        return str(entry)
    return None


_USER_BUCKET_TAG = "users入り"


def _extract_artwork_tags(body):
    """抽取作品標籤清單，並在 aiType==2 時於最前面加上 'AI生成' 標籤。

    過濾掉 Pixiv 自動加上的書籤桶 marker tag（含 ``users入り`` 子字串，例如
    ``5000users入り``）——這類字串不是使用者寫的 tag，是 Pixiv 依書籤數塞進去
    的 metadata。

    回傳前以保序方式 dedup：Pixiv 偶爾會在同一作品的 tag list 中重覆某個 tag
    （例如 `_tag_entry_to_str` 對某些 entry 退而採用 `translation.en` 而與
    另一筆字面 `name` 撞同字串），這裡用 ``dict.fromkeys`` 保留第一次出現的
    順序，重覆者直接丟掉。AI 標籤已先 prepend，所以若 Pixiv tag 中也含
    ``AI生成`` 字串會被去除。
    """
    normalized_tags = []

    ai_label = _ai_type_label(body)
    if ai_label:
        normalized_tags.append(ai_label)

    for entry in _normalize_raw_tags_field(body):
        tag_str = _tag_entry_to_str(entry)
        if tag_str and _USER_BUCKET_TAG not in tag_str:
            normalized_tags.append(tag_str)

    return list(dict.fromkeys(normalized_tags))


def _extract_artwork_pagecount(body, artwork_id):
    """取得 pageCount；本層 body 缺少時退而從 userIllusts[pid] 撈，最終預設為 1。"""
    page_count = body.get('pageCount')
    if page_count is None:
        user_illusts = body.get('userIllusts', {})
        if isinstance(user_illusts, dict):
            illust_info = user_illusts.get(str(artwork_id)) or user_illusts.get(artwork_id) or {}
            if isinstance(illust_info, dict):
                page_count = illust_info.get('pageCount')
    try:
        return int(page_count or 1)
    except (TypeError, ValueError):
        return 1


def _extract_artwork_upload_date(body):
    """讀 body['uploadDate']（Pixiv 真正上傳時間，ISO 8601 含時區）。缺失或空字串時回 None。"""
    val = body.get('uploadDate')
    if not val:
        return None
    return str(val)


def _extract_artwork_create_date(body):
    """讀 body['createDate']（Pixiv 作品建立時間，ISO 8601 含時區）。缺失或空字串時回 None。"""
    val = body.get('createDate')
    if not val:
        return None
    return str(val)


def _extract_artwork_user_id(body):
    """讀 body['userId']（畫師 Pixiv ID 字串）。缺失或空字串時回 None。"""
    val = body.get('userId')
    if val in (None, ''):
        return None
    return str(val)


def _extract_artwork_user_name(body):
    """讀 body['userName']（畫師顯示名稱）。缺失或空字串時回 None。"""
    val = body.get('userName')
    if val in (None, ''):
        return None
    return str(val)


def _extract_artwork_img_url(body):
    """從 body['urls'] 取出原圖 URL；對 multi-page / ugoira 路徑做 p0→p 與 ugoira0→ugoira 修正。"""
    try:
        urls_obj = body.get('urls', {})
        if isinstance(urls_obj, dict):
            # ONLY use 'original'. The old `or urls_obj.get('regular')` fallback
            # was doubly wrong: a 'regular' URL is `..._p0_master1200.jpg` — a
            # non-downloadable preview — and the `.replace("p0","p",1)` below
            # mangles its `_p0_master1200` into `_p_master1200`, so every derived
            # per-page URL 404'd silently. Returning None when 'original' is
            # absent makes `valid` False, which triggers the cookie re-fetch that
            # yields the real 'original' (e.g. for restricted works).
            original_url = urls_obj.get('original')
        else:
            original_url = None
        if not original_url:
            return None
        return str(original_url).replace("p0", "p", 1).replace("ugoira0", "ugoira", 1)
    except Exception:
        return None


def _append_pixiv_info_history(trace_path, pid, trace_entry):
    try:
        history = safe_read_json(trace_path, {})
        if not isinstance(history, dict):
            history = {}
        record = history.get(str(pid))
        if not isinstance(record, dict):
            record = {}
        hist = record.get('history')
        if not isinstance(hist, list):
            hist = []
        hist.append(trace_entry)
        record['history'] = hist[-50:]  # 保留最近 50 次，避免檔案無限長大
        record['latest'] = trace_entry
        record['requires_cookie'] = trace_entry.get('requires_cookie')
        record['artwork_url'] = trace_entry.get('artwork_url')
        record['ajax_url'] = trace_entry.get('ajax_url')
        record['status_no_cookie'] = trace_entry.get('status_no_cookie')
        record['status_cookie'] = trace_entry.get('status_cookie')
        record['result_preview'] = trace_entry.get('result_preview')
        record['checked_at'] = trace_entry.get('checked_at')
        history[str(pid)] = record
        try:
            from safe_io import atomic_write_json
            atomic_write_json(trace_path, history, backup=True)
        except Exception:
            with open(trace_path, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _clean_request_text(value):
    try:
        text = str(value).replace('\ufeff', '').replace('\r', ' ').replace('\n', ' ').strip()
        return text.encode('latin-1', 'ignore').decode('latin-1').strip()
    except Exception:
        try:
            return str(value or '').strip()
        except Exception:
            return ''


def _clean_headers(headers):
    try:
        return {str(k): _clean_request_text(v) for k, v in dict(headers).items()}
    except Exception:
        return headers


def _normalize_artwork_id(raw_value):
    try:
        text = _clean_request_text(raw_value)
    except Exception:
        text = str(raw_value or "").strip()
    if not text:
        return ""
    token = text.rsplit("/", 1)[-1]
    token = token.split("?", 1)[0].split("#", 1)[0].strip()
    m = re.match(r"^(\d+)", token)
    if m:
        return m.group(1)
    return token

# Selenium-dependent login / cookie-grab helpers (_require_selenium /
# logging / auto_get_cookie / get_author_picture_ids) moved to
# pixiv_selenium_login (file-size refactor); re-imported at the bottom of this
# module so ``from pixiv_api import *`` and ``pixiv_api.NAME`` keep resolving.

def Test_cookies(lists,agent):
    cookies=[]
    i=0
    for list1 in lists:
        try:
            pid='96509143'
            headers = {
                'User-Agent': agent,
                'Cookie':list1
                ,'Referer':('http://www.pixiv.net/'+str(pid)),        
                    } 
            url='https://www.pixiv.net/ajax/illust/'+pid+'/pages?lang=zh_tw'            
            htmlfile = requests.get(url,headers=headers,timeout=(10, 30))
            #print(htmlfile.text)
            htmlfile.raise_for_status() 
            #objSoup = bs4.BeautifulSoup(htmlfile.text, 'lxml')
            #print(objSoup.text)
            i=i+1
            cookies.append(list1) 
        except Exception as err:
            print(err)
            pass
    return i,cookies

# get_author_picture_ids moved to pixiv_selenium_login (re-imported below).
# Legacy following-scan free functions (get_follow_illust / illusts /
# thread_no_use_seleium_get_pid) moved to pixiv_legacy_utils (re-imported
# below) — superseded by the worker-thread class methods.

def random_Agent():
    # Updated modern User-Agent list (desktop and mobile, common browsers)
    USER_AGENTS = [
        # Chrome (Windows)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.5790.170 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.179 Safari/537.36",
        # Edge (Chromium)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.1938.81 Safari/537.36 Edg/116.0.1938.81",
        # Firefox
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:118.0) Gecko/20100101 Firefox/118.0",
        # macOS Safari
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
        # Chrome on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.179 Safari/537.36",
        # iPhone (Safari)
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/605.1.15",
        # iPad (Safari)
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/605.1.15",
        # Android Chrome (Pixel)
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.179 Mobile Safari/537.36",
        # Samsung Internet
        "Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-S916B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/21.0 Chrome/115.0.5790.170 Mobile Safari/537.36",
    ]
    return random.choice(USER_AGENTS)

# Pixiv_Tag, the R-18G/exclude tag constants, _pixiv_info_with_retry, the
# get_download_url filter helpers (_is_blocked_r18g_artwork /
# _is_excluded_orientation_tag / _build_per_page_urls) and get_download_url
# itself moved to pixiv_legacy_utils (file-size refactor); re-imported at the
# bottom of this module so the star surface + helper-test imports keep working.
# (_result_preview stays here — it has a live caller in _record_pixiv_info_trace.)

def _result_preview(final_result):
    """Build the small dict logged into pixiv_cookie_requirement.json under ``result_preview``."""
    if not isinstance(final_result, list):
        return {"tags_len": 0, "bookmarkCount": 0, "pageCount": 0, "img_url": None}
    n = len(final_result)
    tags_len = (
        len(final_result[0])
        if n >= 1 and isinstance(final_result[0], list)
        else 0
    )
    return {
        "tags_len": tags_len,
        "bookmarkCount": final_result[1] if n >= 2 else 0,
        "pageCount": final_result[2] if n >= 3 else 0,
        "img_url": final_result[3] if n >= 4 else None,
    }


def _record_pixiv_info_trace(pid_id, ajax_url, requires_cookie,
                              status_no_cookie, status_cookie, final_result):
    """Append a Pixiv_info call to pixiv_cookie_requirement.json (best-effort)."""
    try:
        trace_entry = {
            'artwork_url': 'https://www.pixiv.net/artworks/' + pid_id,
            'pid': str(pid_id),
            'ajax_url': ajax_url,
            'requires_cookie': requires_cookie,
            'status_no_cookie': status_no_cookie,
            'status_cookie': status_cookie,
            'result_preview': _result_preview(final_result),
            'checked_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()),
        }
        trace_path = os.path.join(
            os.getenv('APPDATA') + r'/pixiv_download/',
            'pixiv_cookie_requirement.json',
        )
        os.makedirs(os.path.dirname(trace_path), exist_ok=True)
        _append_pixiv_info_history(trace_path, pid_id, {**trace_entry, 'source': 'fetch'})
    except Exception:
        pass


def _decide_pixiv_info_result(no_cookie_result, no_cookie_valid, cookie, fetch_with_cookie):
    """Decide which fetch result to return based on the no-cookie outcome.

    Returns ``(final_result, requires_cookie, status_cookie)``. ``status_cookie``
    is ``None`` when no cookie fetch was attempted.
    """
    if no_cookie_result == [404]:
        return [404], None, None
    if no_cookie_valid:
        return no_cookie_result, False, None
    if not cookie:
        return no_cookie_result, None, None
    cookie_result, cookie_valid, status_cookie = fetch_with_cookie()
    if cookie_valid:
        return cookie_result, True, status_cookie
    final = cookie_result if cookie_result != [404] else no_cookie_result
    return final, False, status_cookie


def Pixiv_info(url,
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.50'
    ,cookie=None,ip=None, *, session: "requests.Session | None" = None,
    skip_no_cookie=False):                                                #回傳標籤
        url = _clean_request_text(url)
        Agent = _clean_request_text(Agent)
        cookie = _clean_request_text(cookie) if cookie is not None else None
        id = _normalize_artwork_id(url)
        if not str(id).isdigit():
            return [404]

        ajax_url='https://www.pixiv.net/ajax/illust/'+id

        def _parse_payload(payload):
            body = _extract_artwork_body(payload)
            try:
                bookmark_count = int(body.get('bookmarkCount', 0) or 0)
            except (TypeError, ValueError):
                bookmark_count = 0
            page_count = _extract_artwork_pagecount(body, id)
            normalized_tags = _extract_artwork_tags(body)
            img_url = _extract_artwork_img_url(body)
            upload_date = _extract_artwork_upload_date(body)
            create_date = _extract_artwork_create_date(body)
            user_id = _extract_artwork_user_id(body)
            user_name = _extract_artwork_user_name(body)
            result = [
                list(normalized_tags), int(bookmark_count), int(page_count), str(img_url),
                upload_date, create_date, user_id, user_name,
            ]
            valid = bool(img_url) and str(img_url) != 'None'
            return result, valid

        def _fetch(use_cookie=False, retry=0):
            headers = {
                'User-Agent': Agent,
                'referer': 'https://www.pixiv.net/artworks/'+id,
            }
            if use_cookie and cookie:
                headers['Cookie'] = cookie
            headers = _clean_headers(headers)
            try:
                res = (session or requests).get(ajax_url, headers=headers, timeout=20)
            except (requests.exceptions.ProxyError,
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError):
                # Propagate so the scheduler-aware caller can disable the cookie/proxy.
                raise
            except Exception as e:
                # TRANSIENT failure (ReadTimeout, SSL hiccup, malformed socket
                # read, etc.) — NOT a 404. Returning [404] here would make the
                # callers permanently mark_artwork_revoked() a live artwork on a
                # momentary glitch. Return the distinct ["error"] sentinel so the
                # `== [404]` revoke checks skip it and the PID stays pending for
                # retry. (B6)
                print(f"Pixiv_info request error pid={id}: {e}")
                return ["error"], False, -1
            if res.status_code == 404:
                return [404], False, 404
            if res.status_code == 429 and retry < 1:
                print(429)
                time.sleep(60)
                return _fetch(use_cookie=use_cookie, retry=retry+1)
            try:
                payload = res.json()
            except Exception as e:
                # A 200 that didn't parse as JSON (Cloudflare interstitial,
                # truncated body) is transient, not a deletion. Same ["error"]
                # sentinel as above so we never revoke on a parse glitch. (B6)
                print(f"Pixiv_info json error pid={id}: {e}")
                print(f"Pixiv_info response content: {res.text[:500]}")
                print(f"Pixiv_info status code: {res.status_code}")
                return ["error"], False, res.status_code
            parsed, valid = _parse_payload(payload)
            return parsed, valid, res.status_code

        if skip_no_cookie and cookie:
            # 呼叫端（combined 匿名探測）已確認匿名看不到這個作品：直接帶
            # cookie 查，不再重發一次注定失敗的匿名請求。
            cookie_result, cookie_valid, status_cookie = _fetch(use_cookie=True)
            _record_pixiv_info_trace(
                id, ajax_url, True if cookie_valid else None,
                None, status_cookie, cookie_result,
            )
            return cookie_result

        no_cookie_result, no_cookie_valid, status_no_cookie = _fetch(use_cookie=False)
        final_result, requires_cookie, status_cookie = _decide_pixiv_info_result(
            no_cookie_result, no_cookie_valid, cookie,
            lambda: _fetch(use_cookie=True),
        )
        _record_pixiv_info_trace(
            id, ajax_url, requires_cookie,
            status_no_cookie, status_cookie, final_result,
        )
        return final_result

def get_pixiv_cookie_requirement(pid):
    """回傳指定 PID 最近一次是否需要 cookie，找不到時回傳 None。"""
    try:
        trace_path = os.path.join(os.getenv('APPDATA')+r'/pixiv_download/', 'pixiv_cookie_requirement.json')
        data = _load_cookie_requirement_map(trace_path)
        if not data:
            return None
        pid_key = _normalize_artwork_id(pid)
        entry = data.get(str(pid_key))
        if entry is None and str(pid_key) != str(pid):
            entry = data.get(str(pid))
        if isinstance(entry, dict):
            return entry.get('requires_cookie')
    except Exception:
        return None
    return None
    
def userId(url,
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.50'):                                                 #回傳標籤
    # try:
        url = _clean_request_text(url)
        Agent = _clean_request_text(Agent)
        headers = {
            'User-Agent': Agent,
            'referer': 'https://www.pixiv.net/',        
        }
        headers = _clean_headers(headers)
        id=url.rsplit('/',1)[1]
        res = requests.get(url, headers=headers, timeout=(10, 30))
        if res.status_code == 404:
            return 404,404,404,404
        #print(res.json())
        obj = str(bs4.BeautifulSoup(res.text, 'lxml').select_one('meta[name="preload-data"]'))
        obj=obj.replace('<meta content=\'','')
        obj=obj.replace('id="meta-preload-data" name="preload-data"/>','') 
        o=obj.rsplit('\'',1)[0] 
        #print(o)
        o = o.encode('UTF-8')
        data = json.loads(o)
        userId = data['illust'].get(id)
        return userId['userId']

# pixiv_following_count / no_use_seleium_get_pid moved to pixiv_legacy_utils
# (re-imported below).


# ── facade re-exports (file-size refactor) ────────────────────────────────────
# The selenium-login block and the shadowed/legacy free functions were split
# into sibling modules. Re-import them back here so ``from pixiv_api import *``
# (the star surface relied on by thread_following / thread_pid_scan /
# thread_url_fetch / thread_download and the root ``pixiv_api`` shim's
# ``dir(_impl)`` __all__) and direct ``pixiv_api.NAME`` / ``from app.core.
# pixiv_api import NAME`` lookups (incl. the underscore helpers the tests
# import) stay byte-identical. The wildcard imports are placed at the bottom so
# the live HTTP surface above always wins on any (non-existent) name clash.
from app.core.pixiv_selenium_login import (  # noqa: E402,F401  (facade re-export)
    _require_selenium,
    _SELENIUM_AVAILABLE,
    _SELENIUM_IMPORT_ERROR,
    auto_get_cookie,
    get_author_picture_ids,
    logging,
    option,
)
from app.core.pixiv_selenium_login import *  # noqa: E402,F401,F403  (star surface)
from app.core.pixiv_legacy_utils import (  # noqa: E402,F401  (facade re-export)
    _DEFAULT_LIKE_THRESHOLD,
    _EXCLUDE_TAGS,
    _R18G_GORE_TAGS,
    _build_per_page_urls,
    _is_blocked_r18g_artwork,
    _is_excluded_orientation_tag,
    _pixiv_info_with_retry,
    Pixiv_Tag,
    get_download_url,
    get_follow_illust,
    illusts,
    no_use_seleium_get_pid,
    pixiv_following_count,
    thread_no_use_seleium_get_pid,
)
from app.core.pixiv_legacy_utils import *  # noqa: E402,F401,F403  (star surface)
