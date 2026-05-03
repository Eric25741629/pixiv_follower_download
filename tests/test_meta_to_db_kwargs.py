"""Tests for download_thread._meta_to_db_kwargs — meta dict → DB kwargs."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


_ALL_NONE = {
    "tags": None, "like_count": None, "page_count": None,
    "img_url": None, "requires_cookie": None, "updated_at": None,
}


def test_full_meta_dict_translates_one_to_one():
    out = download_thread._meta_to_db_kwargs({
        "tag": ["a", "b"],
        "like": 100,
        "pagecount": 5,
        "img_url": "https://i.pximg.net/x.jpg",
        "requires_cookie": True,
        "updated_at": "2026-05-01T00:00:00",
    })
    assert out == {
        "tags": ["a", "b"],
        "like_count": 100,
        "page_count": 5,
        "img_url": "https://i.pximg.net/x.jpg",
        "requires_cookie": True,
        "updated_at": "2026-05-01T00:00:00",
    }


def test_alias_tags_to_tag():
    """Some legacy callers used 'tags' instead of 'tag'."""
    out = download_thread._meta_to_db_kwargs({"tags": ["x"]})
    assert out["tags"] == ["x"]


def test_alias_pagecount_to_page_count():
    """page_count is an accepted alias for pagecount (covers older serializations)."""
    out = download_thread._meta_to_db_kwargs({"page_count": 9})
    assert out["page_count"] == 9


def test_alias_checked_at_to_updated_at():
    """Older Pixiv_info trace entries used checked_at instead of updated_at."""
    out = download_thread._meta_to_db_kwargs({"checked_at": "2026-01-01T00:00:00"})
    assert out["updated_at"] == "2026-01-01T00:00:00"


def test_tag_takes_precedence_over_tags():
    out = download_thread._meta_to_db_kwargs({"tag": ["primary"], "tags": ["fallback"]})
    assert out["tags"] == ["primary"]


def test_pagecount_takes_precedence_over_page_count():
    out = download_thread._meta_to_db_kwargs({"pagecount": 5, "page_count": 9})
    assert out["page_count"] == 5


def test_updated_at_takes_precedence_over_checked_at():
    out = download_thread._meta_to_db_kwargs({
        "updated_at": "2026-05", "checked_at": "2026-01",
    })
    assert out["updated_at"] == "2026-05"


def test_non_list_tags_become_none():
    out = download_thread._meta_to_db_kwargs({"tag": "a string, not a list"})
    assert out["tags"] is None


def test_none_input_returns_all_none():
    assert download_thread._meta_to_db_kwargs(None) == _ALL_NONE


def test_non_dict_input_returns_all_none():
    assert download_thread._meta_to_db_kwargs("string") == _ALL_NONE
    assert download_thread._meta_to_db_kwargs(42) == _ALL_NONE
    assert download_thread._meta_to_db_kwargs([]) == _ALL_NONE


def test_empty_dict_returns_all_none_fields():
    out = download_thread._meta_to_db_kwargs({})
    assert out == _ALL_NONE


def test_partial_dict_only_populates_provided_fields():
    out = download_thread._meta_to_db_kwargs({"like": 50, "img_url": "u"})
    assert out["like_count"] == 50
    assert out["img_url"] == "u"
    assert out["tags"] is None
    assert out["page_count"] is None
    assert out["requires_cookie"] is None
