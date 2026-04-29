"""Tests for the gif/jpg-shared helpers extracted in Phase 31."""
from pathlib import Path
import sys
import threading
from queue import Queue
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


class _Signal:
    def emit(self, *_):
        pass


def _stub():
    t = download_thread.__new__(download_thread)
    t._output = _Signal()
    t.url_meta = {}
    t.cookies = ""
    t.cookie_pool = []
    t._pid_cookie_selection = {}
    t._cookie_usage_counts = {"step4": {}}
    t._cookie_usage_seen = {}
    t._cookie_alias_map = {}
    t._pid_cookie_used = {}
    t.agent = "test-agent"
    return t


# ── _resolve_pid_and_cookie ──────────────────────────────────────────────────

def test_resolve_pid_extracts_from_artwork_url():
    t = _stub()
    with patch("app.core.thread_download.pixiv_api.get_pixiv_cookie_requirement", return_value=False):
        pid, cookie, need = t._resolve_pid_and_cookie(
            "https://i.pximg.net/img-original/img/2024/01/02/12/00/00/12345_p0.jpg"
        )
    assert pid == "12345"
    assert need is False


def test_resolve_pid_uses_cached_requires_cookie_without_network():
    t = _stub()
    t.url_meta = {"99": {"requires_cookie": True}}
    with patch("app.core.thread_download.pixiv_api.get_pixiv_cookie_requirement") as net:
        pid, _cookie, need = t._resolve_pid_and_cookie(
            "https://i.pximg.net/something/99_p0.jpg"
        )
    assert pid == "99"
    assert need is True
    net.assert_not_called()


def test_resolve_pid_falls_back_to_network_when_cache_lacks_field():
    t = _stub()
    t.url_meta = {"42": {"tag": ["x"]}}  # no requires_cookie field
    with patch("app.core.thread_download.pixiv_api.get_pixiv_cookie_requirement", return_value=True) as net:
        _pid, _cookie, need = t._resolve_pid_and_cookie(
            "https://i.pximg.net/x/42_p0.png"
        )
    assert need is True
    net.assert_called_once_with("42")


# ── _load_artwork_metadata ───────────────────────────────────────────────────

def test_load_metadata_returns_cache_when_present():
    t = _stub()
    t.url_meta = {"7": {"tag": ["a"], "like": 50, "pagecount": 3, "img_url": "u"}}
    with patch("app.core.thread_download.pixiv_api.Pixiv_info") as net:
        result = t._load_artwork_metadata("7", "")
    assert result == (["a"], 50, 3, "u")
    net.assert_not_called()


def test_load_metadata_calls_pixiv_info_on_cache_miss():
    t = _stub()
    t.url_meta = {}
    fake = object()
    with patch("app.core.thread_download.pixiv_api.Pixiv_info", return_value=fake) as net, \
         patch.object(t, "_normalize_pixiv_info", return_value=(["t"], 1, 1, "iu")):
        result = t._load_artwork_metadata("9", "cookie-x")
    assert result == (["t"], 1, 1, "iu")
    net.assert_called_once()
    assert net.call_args.kwargs.get("cookie") == "cookie-x"


def test_load_metadata_skips_cache_when_tag_field_missing():
    """A meta dict without 'tag' is treated as incomplete; fall back to fetch."""
    t = _stub()
    t.url_meta = {"5": {"requires_cookie": False}}  # only requires_cookie, no tag
    fake = object()
    with patch("app.core.thread_download.pixiv_api.Pixiv_info", return_value=fake), \
         patch.object(t, "_normalize_pixiv_info", return_value=(["fetched"], 0, 1, "u")):
        result = t._load_artwork_metadata("5", "")
    assert result == (["fetched"], 0, 1, "u")


# ── _build_artwork_headers ───────────────────────────────────────────────────

def test_headers_omit_cookie_when_not_required():
    t = _stub()
    headers = t._build_artwork_headers("123", "abc", False)
    assert "Cookie" not in headers
    assert headers["Referer"].endswith("/123")


def test_headers_inject_cookie_when_required():
    t = _stub()
    headers = t._build_artwork_headers("123", "abc", True)
    assert headers["Cookie"] == "abc"


def test_headers_honour_pid_used_marker():
    """Second-fetch path in gif: even if need_cookie is False, inject if a
    prior cookied attempt was recorded for this PID."""
    t = _stub()
    t._pid_cookie_used = {"55": True}
    headers = t._build_artwork_headers("55", "abc", False, honour_pid_used=True)
    assert headers["Cookie"] == "abc"


def test_headers_no_cookie_when_pool_empty():
    t = _stub()
    headers = t._build_artwork_headers("123", "", True)
    assert "Cookie" not in headers
