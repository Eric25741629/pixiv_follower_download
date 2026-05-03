"""Tests for the helpers extracted from pixiv_api.get_download_url."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pixiv_api import (
    _build_per_page_urls,
    _is_blocked_r18g_artwork,
    _is_excluded_orientation_tag,
)


# ── _is_blocked_r18g_artwork ────────────────────────────────────────────────

def test_r18g_with_gore_marker_blocked():
    assert _is_blocked_r18g_artwork("R-18G,死姦,オリジナル") is True
    assert _is_blocked_r18g_artwork("R-18G,necrophilia") is True
    assert _is_blocked_r18g_artwork("R-18G,食糞,他のtag") is True


def test_r18g_without_gore_marker_allowed():
    """R-18G without specific gore markers is NOT blocked by this filter."""
    assert _is_blocked_r18g_artwork("R-18G,オリジナル") is False


def test_plain_r18_not_blocked_by_r18g_filter():
    """The filter only fires on R-18G + gore — plain R-18 must pass through."""
    assert _is_blocked_r18g_artwork("R-18,オリジナル,死姦") is False


def test_safe_artwork_passes():
    assert _is_blocked_r18g_artwork("オリジナル,風景") is False


def test_handles_list_input_via_str():
    """The function uses str() so passing a list still works."""
    assert _is_blocked_r18g_artwork(["R-18G", "死姦"]) is True


# ── _is_excluded_orientation_tag ────────────────────────────────────────────

def test_gay_tag_excluded():
    assert _is_excluded_orientation_tag("gay,オリジナル") is True


def test_bl_original_tag_excluded():
    assert _is_excluded_orientation_tag("原創BL,オリジナル") is True


def test_safe_tags_not_excluded():
    assert _is_excluded_orientation_tag("オリジナル,風景") is False


# ── _build_per_page_urls ────────────────────────────────────────────────────

def test_build_per_page_urls_single_page():
    urls = _build_per_page_urls(
        "https://i.pximg.net/img-original/img/.../12345_p.png", 1
    )
    assert urls == ["https://i.pximg.net/img-original/img/.../12345_p0.png"]


def test_build_per_page_urls_multi_page():
    urls = _build_per_page_urls(
        "https://i.pximg.net/img-original/img/.../12345_p.jpg", 3
    )
    assert urls == [
        "https://i.pximg.net/img-original/img/.../12345_p0.jpg",
        "https://i.pximg.net/img-original/img/.../12345_p1.jpg",
        "https://i.pximg.net/img-original/img/.../12345_p2.jpg",
    ]


def test_build_per_page_urls_zero_pages():
    assert _build_per_page_urls("anything.png", 0) == []
