"""Tests for the helpers extracted from get_img_url_thread.get_download_url."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_url_fetch import get_img_url_thread


# ── _step3_cache_is_usable (pure-function, no instance state needed) ────────

def test_cache_is_usable_with_valid_entry():
    cached = {"img_url": "https://i.pximg.net/x_p.jpg", "pagecount": 3}
    assert get_img_url_thread._step3_cache_is_usable(cached) is True


def test_cache_is_usable_returns_false_for_none_img_url():
    assert get_img_url_thread._step3_cache_is_usable({"img_url": None, "pagecount": 1}) is False


def test_cache_is_usable_returns_false_for_string_None_img_url():
    """The literal string 'None' shows up when older code stringified None into the JSON cache."""
    assert get_img_url_thread._step3_cache_is_usable({"img_url": "None", "pagecount": 1}) is False


def test_cache_is_usable_returns_false_for_empty_img_url():
    assert get_img_url_thread._step3_cache_is_usable({"img_url": "", "pagecount": 1}) is False


def test_cache_is_usable_returns_false_for_zero_pagecount():
    assert get_img_url_thread._step3_cache_is_usable({"img_url": "https://x", "pagecount": 0}) is False


def test_cache_is_usable_returns_false_for_negative_pagecount():
    assert get_img_url_thread._step3_cache_is_usable({"img_url": "https://x", "pagecount": -1}) is False


def test_cache_is_usable_handles_string_pagecount():
    assert get_img_url_thread._step3_cache_is_usable({"img_url": "https://x", "pagecount": "2"}) is True


def test_cache_is_usable_handles_garbage_pagecount():
    """Non-numeric pagecount must not crash; should be treated as missing."""
    assert get_img_url_thread._step3_cache_is_usable({"img_url": "https://x", "pagecount": "abc"}) is False


def test_cache_is_usable_returns_false_for_non_dict():
    assert get_img_url_thread._step3_cache_is_usable(None) is False
    assert get_img_url_thread._step3_cache_is_usable([]) is False
    assert get_img_url_thread._step3_cache_is_usable("string") is False


def test_cache_is_usable_returns_false_for_empty_dict():
    assert get_img_url_thread._step3_cache_is_usable({}) is False
