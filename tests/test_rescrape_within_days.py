"""Tests for the rescrape-within-days cache staleness check.

Covers three new symbols on `get_img_url_thread`:
- `_parse_pixiv_upload_date` (staticmethod)
- `_coerce_rescrape_days` (staticmethod)
- `_is_within_rescrape_window` (instance method, reads `self._rescrape_within_days`)
- `_step3_cache_is_fresh` (instance method, combines `_step3_cache_is_usable` with the window check)

Instance methods are exercised by constructing the class via ``__new__`` to
avoid the full ``__init__`` pipeline (matches the pattern used elsewhere in
the test suite for static helpers).
"""
from __future__ import annotations

import datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_url_fetch import get_img_url_thread


def _make_instance(threshold_days: int):
    inst = get_img_url_thread.__new__(get_img_url_thread)
    inst._rescrape_within_days = int(threshold_days)
    return inst


def _iso(days_ago: float) -> str:
    """Return an ISO-8601 timestamp `days_ago` days before now in JST (+09:00)."""
    when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    jst = datetime.timezone(datetime.timedelta(hours=9))
    return when.astimezone(jst).isoformat()


# ── _coerce_rescrape_days ───────────────────────────────────────────────────

def test_coerce_rescrape_days_positive_passes_through():
    assert get_img_url_thread._coerce_rescrape_days(365) == 365
    assert get_img_url_thread._coerce_rescrape_days("30") == 30


def test_coerce_rescrape_days_zero():
    assert get_img_url_thread._coerce_rescrape_days(0) == 0


def test_coerce_rescrape_days_negative_becomes_zero():
    assert get_img_url_thread._coerce_rescrape_days(-1) == 0
    assert get_img_url_thread._coerce_rescrape_days(-365) == 0


def test_coerce_rescrape_days_garbage_becomes_zero():
    assert get_img_url_thread._coerce_rescrape_days("abc") == 0
    assert get_img_url_thread._coerce_rescrape_days(None) == 0
    assert get_img_url_thread._coerce_rescrape_days("") == 0
    assert get_img_url_thread._coerce_rescrape_days([]) == 0


# ── _parse_pixiv_upload_date ────────────────────────────────────────────────

def test_parse_upload_date_none_and_empty():
    assert get_img_url_thread._parse_pixiv_upload_date(None) is None
    assert get_img_url_thread._parse_pixiv_upload_date("") is None
    assert get_img_url_thread._parse_pixiv_upload_date("   ") is None


def test_parse_upload_date_non_string():
    assert get_img_url_thread._parse_pixiv_upload_date(0) is None
    assert get_img_url_thread._parse_pixiv_upload_date(1234567890) is None
    assert get_img_url_thread._parse_pixiv_upload_date({"x": 1}) is None


def test_parse_upload_date_unparseable():
    assert get_img_url_thread._parse_pixiv_upload_date("not a date") is None
    assert get_img_url_thread._parse_pixiv_upload_date("2024/01/15") is None


def test_parse_upload_date_iso_with_timezone():
    dt = get_img_url_thread._parse_pixiv_upload_date("2024-01-15T12:30:00+09:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2024 and dt.month == 1 and dt.day == 15


def test_parse_upload_date_naive_treated_as_utc():
    dt = get_img_url_thread._parse_pixiv_upload_date("2024-01-15T12:30:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset() == datetime.timedelta(0)


def test_parse_upload_date_z_suffix():
    """'Z' (Zulu / UTC) is valid ISO-8601 but Python 3.10's fromisoformat
    rejects it. Fallback parser must handle it."""
    dt = get_img_url_thread._parse_pixiv_upload_date("2024-01-15T12:30:00Z")
    assert dt is not None
    assert dt.utcoffset() == datetime.timedelta(0)


def test_parse_upload_date_compact_offset():
    """'+0900' (no colon) is valid ISO-8601 but Python 3.10's fromisoformat
    can reject it. Fallback parser must handle it."""
    dt = get_img_url_thread._parse_pixiv_upload_date("2024-01-15T12:30:00+0900")
    assert dt is not None
    assert dt.utcoffset() == datetime.timedelta(hours=9)


def test_parse_upload_date_microseconds_with_offset():
    """Fractional seconds + offset, also a path the fallback covers."""
    dt = get_img_url_thread._parse_pixiv_upload_date("2024-01-15T12:30:00.123456+09:00")
    assert dt is not None
    assert dt.year == 2024


# ── _is_within_rescrape_window ──────────────────────────────────────────────

def test_window_disabled_when_threshold_zero():
    inst = _make_instance(0)
    assert inst._is_within_rescrape_window({"upload_date": _iso(10)}) is False


def test_window_disabled_when_threshold_negative():
    inst = _make_instance(-5)
    assert inst._is_within_rescrape_window({"upload_date": _iso(10)}) is False


def test_window_false_for_non_dict():
    inst = _make_instance(365)
    assert inst._is_within_rescrape_window(None) is False
    assert inst._is_within_rescrape_window([]) is False
    assert inst._is_within_rescrape_window("str") is False


def test_window_false_for_missing_upload_date():
    inst = _make_instance(365)
    assert inst._is_within_rescrape_window({}) is False
    assert inst._is_within_rescrape_window({"upload_date": None}) is False


def test_window_false_for_unparseable_upload_date():
    inst = _make_instance(365)
    assert inst._is_within_rescrape_window({"upload_date": "garbage"}) is False


def test_window_true_for_recent_upload():
    inst = _make_instance(365)
    assert inst._is_within_rescrape_window({"upload_date": _iso(100)}) is True
    assert inst._is_within_rescrape_window({"upload_date": _iso(1)}) is True


def test_window_false_for_old_upload():
    inst = _make_instance(365)
    assert inst._is_within_rescrape_window({"upload_date": _iso(400)}) is False
    assert inst._is_within_rescrape_window({"upload_date": _iso(2000)}) is False


def test_window_boundary_at_exactly_threshold():
    inst = _make_instance(365)
    # 365 days ago — comparison is strict `<`, so equal-to-threshold is OUTSIDE window.
    just_outside = _iso(365.0)
    assert inst._is_within_rescrape_window({"upload_date": just_outside}) is False
    # 364.9 days ago — clearly inside.
    inside = _iso(364.9)
    assert inst._is_within_rescrape_window({"upload_date": inside}) is True


def test_window_false_for_future_upload_date():
    inst = _make_instance(365)
    future = _iso(-2.0)  # 2 days from now
    assert inst._is_within_rescrape_window({"upload_date": future}) is False


# ── _step3_cache_is_fresh ───────────────────────────────────────────────────

def test_cache_fresh_false_for_malformed_cache():
    inst = _make_instance(365)
    assert inst._step3_cache_is_fresh({}) is False
    assert inst._step3_cache_is_fresh({"img_url": None, "pagecount": 1}) is False
    assert inst._step3_cache_is_fresh({"img_url": "https://x", "pagecount": 0}) is False


def test_cache_fresh_false_for_recent_artwork_within_window():
    inst = _make_instance(365)
    cached = {
        "img_url": "https://i.pximg.net/x_p.jpg",
        "pagecount": 3,
        "upload_date": _iso(100),
    }
    assert inst._step3_cache_is_fresh(cached) is False


def test_cache_fresh_true_for_old_artwork_outside_window():
    inst = _make_instance(365)
    cached = {
        "img_url": "https://i.pximg.net/x_p.jpg",
        "pagecount": 3,
        "upload_date": _iso(400),
    }
    assert inst._step3_cache_is_fresh(cached) is True


def test_cache_fresh_true_when_feature_disabled():
    inst = _make_instance(0)
    cached = {
        "img_url": "https://i.pximg.net/x_p.jpg",
        "pagecount": 3,
        "upload_date": _iso(1),  # would be inside window if enabled
    }
    assert inst._step3_cache_is_fresh(cached) is True


def test_cache_fresh_true_when_upload_date_missing():
    inst = _make_instance(365)
    cached = {"img_url": "https://i.pximg.net/x_p.jpg", "pagecount": 3}
    assert inst._step3_cache_is_fresh(cached) is True


def test_cache_fresh_true_when_upload_date_malformed():
    inst = _make_instance(365)
    cached = {
        "img_url": "https://i.pximg.net/x_p.jpg",
        "pagecount": 3,
        "upload_date": "garbage",
    }
    assert inst._step3_cache_is_fresh(cached) is True
