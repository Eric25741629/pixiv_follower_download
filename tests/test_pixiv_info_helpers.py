"""Tests for the helpers extracted from pixiv_api.Pixiv_info."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pixiv_api import _decide_pixiv_info_result, _result_preview


# ── _result_preview ─────────────────────────────────────────────────────────

def test_result_preview_full_shape():
    out = _result_preview([["tag1", "tag2", "tag3"], 42, 5, "https://i.pximg.net/x.jpg"])
    assert out == {
        "tags_len": 3,
        "bookmarkCount": 42,
        "pageCount": 5,
        "img_url": "https://i.pximg.net/x.jpg",
    }


def test_result_preview_tags_not_a_list():
    """Defensive: tags slot may have been mangled — fall back to 0."""
    out = _result_preview(["not-a-list", 1, 2, "u"])
    assert out["tags_len"] == 0


def test_result_preview_short_list():
    out = _result_preview([["a", "b"]])
    assert out == {
        "tags_len": 2,
        "bookmarkCount": 0,
        "pageCount": 0,
        "img_url": None,
    }


def test_result_preview_404_sentinel():
    """Pixiv_info returns [404] on 404; preview must not crash on it."""
    out = _result_preview([404])
    assert out == {
        "tags_len": 0,
        "bookmarkCount": 0,
        "pageCount": 0,
        "img_url": None,
    }


def test_result_preview_non_list_input():
    out = _result_preview(None)
    assert out == {"tags_len": 0, "bookmarkCount": 0, "pageCount": 0, "img_url": None}


# ── _decide_pixiv_info_result ───────────────────────────────────────────────

class _FetchSentinel:
    """Records whether the with-cookie fetch was actually invoked."""
    def __init__(self, response):
        self.calls = 0
        self.response = response

    def __call__(self):
        self.calls += 1
        return self.response


def test_decide_404_short_circuits():
    """A 404 from no-cookie fetch never tries cookie."""
    fetch_with = _FetchSentinel(("unused", True, 200))
    final, req, status = _decide_pixiv_info_result(
        no_cookie_result=[404],
        no_cookie_valid=False,
        cookie="any",
        fetch_with_cookie=fetch_with,
    )
    assert final == [404]
    assert req is None
    assert status is None
    assert fetch_with.calls == 0


def test_decide_no_cookie_valid_returns_no_cookie_result():
    fetch_with = _FetchSentinel(("unused", True, 200))
    final, req, status = _decide_pixiv_info_result(
        no_cookie_result=[["tag"], 1, 1, "u"],
        no_cookie_valid=True,
        cookie="any",
        fetch_with_cookie=fetch_with,
    )
    assert final == [["tag"], 1, 1, "u"]
    assert req is False
    assert status is None
    assert fetch_with.calls == 0


def test_decide_no_cookie_invalid_no_cookie_provided():
    """No cookie configured → can't retry; returns no-cookie result with requires_cookie=None."""
    fetch_with = _FetchSentinel(("unused", True, 200))
    final, req, status = _decide_pixiv_info_result(
        no_cookie_result=[["tag"], 1, 1, "None"],
        no_cookie_valid=False,
        cookie=None,
        fetch_with_cookie=fetch_with,
    )
    assert req is None
    assert status is None
    assert fetch_with.calls == 0


def test_decide_with_cookie_succeeds():
    """No-cookie returned invalid; cookie retry succeeds → requires_cookie=True."""
    fetch_with = _FetchSentinel(([["tag"], 5, 2, "https://valid"], True, 200))
    final, req, status = _decide_pixiv_info_result(
        no_cookie_result=[["tag"], 0, 0, "None"],
        no_cookie_valid=False,
        cookie="real-cookie",
        fetch_with_cookie=fetch_with,
    )
    assert final == [["tag"], 5, 2, "https://valid"]
    assert req is True
    assert status == 200
    assert fetch_with.calls == 1


def test_decide_with_cookie_fails_falls_back_to_no_cookie():
    """Both fetches invalid → return cookie result (preserves status), requires_cookie=False."""
    fetch_with = _FetchSentinel(([["tag"], 0, 0, "None"], False, 403))
    final, req, status = _decide_pixiv_info_result(
        no_cookie_result=[["tag"], 0, 0, "None"],
        no_cookie_valid=False,
        cookie="cookie",
        fetch_with_cookie=fetch_with,
    )
    assert final == [["tag"], 0, 0, "None"]
    assert req is False
    assert status == 403


def test_decide_cookie_returns_404_falls_back_to_no_cookie_result():
    """If cookie attempt 404s but no-cookie didn't, prefer the original no-cookie payload."""
    fetch_with = _FetchSentinel(([404], False, 404))
    no_cookie = [["tag"], 1, 1, "None"]
    final, req, status = _decide_pixiv_info_result(
        no_cookie_result=no_cookie,
        no_cookie_valid=False,
        cookie="cookie",
        fetch_with_cookie=fetch_with,
    )
    assert final == no_cookie
    assert req is False
    assert status == 404
