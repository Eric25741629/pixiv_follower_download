"""Tests for the helpers extracted from _migrate_url_meta_schema."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_url_fetch import get_img_url_thread


# ── _resolve_required_cookie_value (static, pure) ──────────────────────────

def test_resolve_uses_meta_when_set():
    """meta-level requires_cookie wins over pinfo and saved trace."""
    out = get_img_url_thread._resolve_required_cookie_value(
        meta={"requires_cookie": True},
        pinfo={"requires_cookie": False},
        saved_req_map={"100": False},
        pid_norm="100",
    )
    assert out is True


def test_resolve_falls_back_to_pinfo_when_meta_unset():
    out = get_img_url_thread._resolve_required_cookie_value(
        meta={"requires_cookie": None},
        pinfo={"requires_cookie": True},
        saved_req_map={"100": False},
        pid_norm="100",
    )
    assert out is True


def test_resolve_falls_back_to_saved_when_meta_and_pinfo_unset():
    out = get_img_url_thread._resolve_required_cookie_value(
        meta={"requires_cookie": None},
        pinfo={"requires_cookie": None},
        saved_req_map={"100": True},
        pid_norm="100",
    )
    assert out is True


def test_resolve_returns_none_when_no_source_has_value():
    out = get_img_url_thread._resolve_required_cookie_value(
        meta={"requires_cookie": None},
        pinfo={"requires_cookie": None},
        saved_req_map={},
        pid_norm="100",
    )
    assert out is None


def test_resolve_handles_pinfo_not_a_dict():
    out = get_img_url_thread._resolve_required_cookie_value(
        meta={"requires_cookie": None},
        pinfo="not-a-dict",
        saved_req_map={"100": False},
        pid_norm="100",
    )
    assert out is False


def test_resolve_handles_saved_req_map_not_a_dict():
    """Defensive: should not crash if saved_req_map is non-dict garbage."""
    out = get_img_url_thread._resolve_required_cookie_value(
        meta={"requires_cookie": None},
        pinfo=None,
        saved_req_map="not-a-dict",
        pid_norm="100",
    )
    assert out is None


# ── _build_migrated_pixiv_info (static, pure) ──────────────────────────────

def test_build_migrated_pixiv_info_pulls_legacy_top_level_fields():
    out = get_img_url_thread._build_migrated_pixiv_info(
        meta={"tag": ["a", "b"], "like": 100, "pagecount": 3, "img_url": "u"},
        req=True,
    )
    assert out == {
        "tag": ["a", "b"],
        "like": 100,
        "pagecount": 3,
        "img_url": "u",
        "requires_cookie": True,
        "queried_at": "",
        "source": "migrated",
    }


def test_build_migrated_pixiv_info_replaces_non_list_tag_with_empty():
    """A 404 sentinel or string in tag must be replaced with []."""
    out = get_img_url_thread._build_migrated_pixiv_info(
        meta={"tag": 404, "like": 0, "pagecount": 0, "img_url": None},
        req=None,
    )
    assert out["tag"] == []


def test_build_migrated_pixiv_info_uses_defaults_when_fields_missing():
    out = get_img_url_thread._build_migrated_pixiv_info(meta={}, req=False)
    assert out == {
        "tag": [],
        "like": 0,
        "pagecount": 0,
        "img_url": None,
        "requires_cookie": False,
        "queried_at": "",
        "source": "migrated",
    }


# ── _migrate_one_url_meta_entry ────────────────────────────────────────────

def _stub_thread(tmp_path):
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.url_meta = {}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t.path = str(tmp_path)
    return t


def test_migrate_one_returns_false_for_non_dict(tmp_path):
    t = _stub_thread(tmp_path)
    assert t._migrate_one_url_meta_entry("100", "not-a-dict", {}) is False


def test_migrate_one_adds_pixiv_info_when_missing(tmp_path):
    t = _stub_thread(tmp_path)
    meta = {"tag": ["a"], "like": 5, "pagecount": 1, "img_url": "u"}
    t.url_meta["100"] = meta
    changed = t._migrate_one_url_meta_entry("100", meta, {})
    assert changed is True
    # pixiv_info synthesized from top-level fields
    assert isinstance(t.url_meta["100"]["pixiv_info"], dict)
    assert t.url_meta["100"]["pixiv_info"]["source"] == "migrated"


def test_migrate_one_propagates_saved_requires_cookie(tmp_path):
    t = _stub_thread(tmp_path)
    meta = {"tag": ["a"]}
    t.url_meta["100"] = meta
    changed = t._migrate_one_url_meta_entry(
        "100", meta, {"100": True}
    )
    assert changed is True
    assert t.url_meta["100"]["requires_cookie"] is True
    assert t.url_meta["100"]["pixiv_info"]["requires_cookie"] is True


def test_migrate_one_no_change_when_already_migrated(tmp_path):
    t = _stub_thread(tmp_path)
    meta = {
        "tag": ["a"],
        "like": 5,
        "pagecount": 1,
        "img_url": "u",
        "requires_cookie": False,
        "pixiv_info": {
            "tag": ["a"], "like": 5, "pagecount": 1, "img_url": "u",
            "requires_cookie": False, "queried_at": "", "source": "fetch",
        },
    }
    t.url_meta["100"] = meta
    assert t._migrate_one_url_meta_entry("100", meta, {}) is False


def test_migrate_one_updates_pinfo_requires_cookie_when_meta_overrides(tmp_path):
    """When meta-level requires_cookie disagrees with pinfo, pinfo gets updated."""
    t = _stub_thread(tmp_path)
    meta = {
        "tag": ["a"],
        "requires_cookie": True,  # meta says True
        "pixiv_info": {
            "tag": ["a"], "requires_cookie": False, "source": "fetch",
        },
    }
    t.url_meta["100"] = meta
    changed = t._migrate_one_url_meta_entry("100", meta, {})
    assert changed is True
    assert t.url_meta["100"]["pixiv_info"]["requires_cookie"] is True
