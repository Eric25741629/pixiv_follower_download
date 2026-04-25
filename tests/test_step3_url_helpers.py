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
