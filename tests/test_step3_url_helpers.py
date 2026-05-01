"""Tests for get_img_url_thread helper methods (Phase 7 refactoring)."""
from pathlib import Path
import sys
import threading
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_url_fetch import get_img_url_thread


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


def _stub():
    t = get_img_url_thread.__new__(get_img_url_thread)
    t._output = _Signal()
    t._ban_tag_norm = []
    t._must_tag_norm = []
    t.like_num = 0
    t.special_like_rules = []
    t.tag_queue = Queue()
    t.like_queue = Queue()
    t._step3_filter_skip_counts = {"ban_tag": 0, "must_tag": 0, "like": 0}
    t._step3_filter_skip_notice_emitted = False
    t._step3_filter_skip_every = 200
    t.url_meta = {}
    t._pid_cache_hit = {}
    t.pid_wait_min = 10
    t.pid_wait_max = 60
    t.pid_wait_nocookie_min = 1
    t.pid_wait_nocookie_max = 6
    t.cookie_pool = []
    t.path = "/tmp"
    return t


# ── _passes_artwork_filters ──────────────────────────────────────────────────

def test_passes_no_filters():
    t = _stub()
    ok, reason = t._passes_artwork_filters("123", ["art", "cute"], 100)
    assert ok is True
    assert reason == "pass"


def test_ban_tag_blocks():
    t = _stub()
    t._ban_tag_norm = ["explicit"]
    ok, reason = t._passes_artwork_filters("123", ["Explicit", "art"], 100)
    assert ok is False
    assert reason == "ban_tag"
    assert t.tag_queue.qsize() == 1


def test_must_tag_blocks_when_missing():
    t = _stub()
    t._must_tag_norm = ["fanart"]
    ok, reason = t._passes_artwork_filters("123", ["cute"], 100)
    assert ok is False
    assert reason == "must_tag"


def test_must_tag_passes_when_present():
    t = _stub()
    t._must_tag_norm = ["fanart"]
    ok, reason = t._passes_artwork_filters("123", ["fanart", "cute"], 100)
    assert ok is True


def test_like_threshold_blocks():
    t = _stub()
    t.like_num = 1000
    ok, reason = t._passes_artwork_filters("123", ["art"], 500)
    assert ok is False
    assert reason == "like"
    assert t.like_queue.qsize() == 1


def test_like_threshold_passes():
    t = _stub()
    t.like_num = 100
    ok, reason = t._passes_artwork_filters("123", ["art"], 100)
    assert ok is True


def test_like_threshold_zero_always_passes():
    t = _stub()
    t.like_num = 0
    ok, reason = t._passes_artwork_filters("123", ["art"], 0)
    assert ok is True


def test_ban_tag_case_insensitive():
    t = _stub()
    t._ban_tag_norm = ["r18"]
    ok, _ = t._passes_artwork_filters("123", ["R18", "art"], 100)
    assert ok is False


def test_ban_tag_substring_match():
    t = _stub()
    t._ban_tag_norm = ["rape"]
    # "rape" contained in "rape_fantasy"
    ok, _ = t._passes_artwork_filters("123", ["rape_fantasy"], 100)
    assert ok is False


# ── _build_cached_urls_from_meta ─────────────────────────────────────────────

def test_build_cached_urls_single_page():
    t = _stub()
    meta = {"img_url": "https://i.pximg.net/img-original/1234_p0.jpg", "pagecount": 1}
    urls = t._build_cached_urls_from_meta("1234", meta)
    assert urls == ["https://i.pximg.net/img-original/1234_p0.jpg"]


def test_build_cached_urls_multi_page():
    t = _stub()
    meta = {"img_url": "https://i.pximg.net/img-original/1234_p0.jpg", "pagecount": 3}
    urls = t._build_cached_urls_from_meta("1234", meta)
    assert len(urls) == 3
    assert "1234_p0.jpg" in urls[0]
    assert "1234_p1.jpg" in urls[1]
    assert "1234_p2.jpg" in urls[2]


def test_build_cached_urls_empty_meta():
    t = _stub()
    urls = t._build_cached_urls_from_meta("1234", {})
    assert urls == []


def test_build_cached_urls_no_img_url():
    t = _stub()
    meta = {"img_url": None, "pagecount": 3}
    urls = t._build_cached_urls_from_meta("1234", meta)
    assert urls == []


# ── _reset_run_counters ──────────────────────────────────────────────────────

def test_reset_run_counters():
    t = _stub()
    t._step3_query_counts = {"network": 99, "cache": 99, "skip": 99}
    t._step3_cookie_req_counts = {"need": 99, "free": 99, "unknown": 99}
    t._step3_wait_applied_count = 99
    t._reset_run_counters()
    assert t._step3_query_counts == {"network": 0, "cache": 0, "skip": 0}
    assert t._step3_cookie_req_counts == {"need": 0, "free": 0, "unknown": 0}
    assert t._step3_wait_applied_count == 0


# ── _is_pid_cached_meta ──────────────────────────────────────────────────────

def test_is_pid_cached_meta_hit():
    t = _stub()
    t.url_meta = {"123": {"img_url": "https://example.com/123_p0.jpg", "pagecount": 2}}
    hit, meta = t._is_pid_cached_meta("123")
    assert hit is True
    assert meta["pagecount"] == 2


def test_is_pid_cached_meta_miss_no_url():
    t = _stub()
    t.url_meta = {"123": {"img_url": "", "pagecount": 2}}
    hit, _ = t._is_pid_cached_meta("123")
    assert hit is False


def test_is_pid_cached_meta_miss_zero_pagecount():
    t = _stub()
    t.url_meta = {"123": {"img_url": "https://example.com/123_p0.jpg", "pagecount": 0}}
    hit, _ = t._is_pid_cached_meta("123")
    assert hit is False


def test_step3_get_download_url_uses_cookie_override(monkeypatch):
    """When cookie_override is provided, get_download_url passes it to Pixiv_info."""
    import queue as _q
    from app.core import thread_url_fetch as turl
    import pixiv_api

    captured = {"cookie": None, "session": None}

    def fake_pixiv_info(url, Agent=None, cookie=None, *, session=None):
        captured["cookie"] = cookie
        captured["session"] = session
        return None  # treated as fetch failure; we only care about kwargs

    monkeypatch.setattr(pixiv_api, "Pixiv_info", fake_pixiv_info)
    monkeypatch.setattr(turl, "Pixiv_info", fake_pixiv_info)

    t = turl.get_img_url_thread.__new__(turl.get_img_url_thread)
    # bare-bones init: only set what get_download_url touches when info is None
    t._stop_event = __import__("threading").Event()
    t._pause_event = __import__("threading").Event()
    t._pause_event.set()
    t._q = _q.Queue()
    t.path = "."
    t._pid_cookie_selection = {}
    t._pid_cookie_alias_selection = {}
    t.cookie_pool = []
    t._cookie_alias_map = {}
    t.cookies = ""
    t.url_meta = {}
    t._pid_cookie_used = {}
    t._pid_cache_hit = {}
    t.exist_pid = set()
    t._cookie_usage_counts = {"step3": {}, "step4": {}}
    t._cookie_usage_seen = {"step3": set(), "step4": set()}

    # Run with cookie_override
    sentinel_session = object()
    try:
        t.get_download_url(".", "agent", 1, "777",
                           cookie_override="OVERRIDE_COOKIE",
                           session=sentinel_session)
    except Exception:
        pass  # downstream errors after Pixiv_info returns None are out of scope

    assert captured["cookie"] == "OVERRIDE_COOKIE"
    assert captured["session"] is sentinel_session


def test_pixiv_info_propagates_proxy_error():
    """Pixiv_info must propagate ProxyError so scheduler can disable account."""
    import pytest
    import requests
    from app.core import pixiv_api

    class FailSession:
        def __init__(self):
            pass
        def get(self, *a, **kw):
            raise requests.exceptions.ProxyError("simulated dead proxy")

    with pytest.raises(requests.exceptions.ProxyError):
        pixiv_api.Pixiv_info(
            "https://www.pixiv.net/artworks/777",
            "agent",
            cookie="c",
            session=FailSession(),
        )


def test_get_download_url_propagates_proxy_error(monkeypatch):
    """ProxyError from Pixiv_info must propagate out of get_download_url
    so _run_processing_loop can release(ok=False)."""
    import pytest
    import requests
    from app.core import thread_url_fetch as turl
    import pixiv_api

    def fail_pixiv_info(url, Agent=None, cookie=None, *, session=None):
        raise requests.exceptions.ProxyError("dead proxy mid-fetch")

    monkeypatch.setattr(pixiv_api, "Pixiv_info", fail_pixiv_info)
    monkeypatch.setattr(turl, "Pixiv_info", fail_pixiv_info)

    t = turl.get_img_url_thread.__new__(turl.get_img_url_thread)
    t._stop_event = __import__("threading").Event()
    t._pause_event = __import__("threading").Event()
    t._pause_event.set()
    t._q = __import__("queue").Queue()
    t.path = "."
    t._pid_cookie_selection = {}
    t._pid_cookie_alias_selection = {}
    t.cookie_pool = []
    t._cookie_alias_map = {}
    t.cookies = ""
    t.url_meta = {}
    t._pid_cookie_used = {}
    t._pid_cache_hit = {}
    t._cookie_usage_counts = {"step3": {}}
    t._cookie_usage_seen = {"step3": set()}
    t.exist_pid = set()

    with pytest.raises(requests.exceptions.ProxyError):
        t.get_download_url(".", "agent", 1, "777", cookie_override="c", session=None)
