"""Tests for cookie-requirement JSON merge logic in thread_url_fetch."""
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_url_fetch import get_img_url_thread


def _stub(tmp_path):
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.path = str(tmp_path)
    return t


# ── _merge_cookie_requirement_file ──────────────────────────────────────────

def test_merge_loads_simple_entries(tmp_path):
    t = _stub(tmp_path)
    f = tmp_path / "in.json"
    f.write_text(json.dumps({
        "100": {"requires_cookie": True},
        "200": {"requires_cookie": False},
        "300": {"requires_cookie": None},
    }), encoding="utf-8")
    merged = {}
    t._merge_cookie_requirement_file(str(f), merged)
    assert merged == {"100": True, "200": False, "300": None}


def test_merge_first_writer_wins_for_concrete_value(tmp_path):
    """If a PID is already merged with a real True/False, a later file does NOT overwrite."""
    t = _stub(tmp_path)
    f = tmp_path / "in.json"
    f.write_text(json.dumps({"100": {"requires_cookie": False}}), encoding="utf-8")
    merged = {"100": True}
    t._merge_cookie_requirement_file(str(f), merged)
    assert merged["100"] is True


def test_merge_can_upgrade_none_to_concrete_value(tmp_path):
    """A merged-None entry CAN be replaced by a concrete True/False from a fallback file."""
    t = _stub(tmp_path)
    f = tmp_path / "in.json"
    f.write_text(json.dumps({"100": {"requires_cookie": True}}), encoding="utf-8")
    merged = {"100": None}
    t._merge_cookie_requirement_file(str(f), merged)
    assert merged["100"] is True


def test_merge_skips_invalid_requires_cookie_value(tmp_path):
    t = _stub(tmp_path)
    f = tmp_path / "in.json"
    f.write_text(json.dumps({
        "100": {"requires_cookie": "yes"},  # invalid
        "200": {"requires_cookie": 42},     # invalid
        "300": {"requires_cookie": True},   # ok
    }), encoding="utf-8")
    merged = {}
    t._merge_cookie_requirement_file(str(f), merged)
    assert merged == {"300": True}


def test_merge_skips_non_dict_entries(tmp_path):
    t = _stub(tmp_path)
    f = tmp_path / "in.json"
    f.write_text(json.dumps({
        "100": "not-a-dict",
        "200": ["list"],
        "300": {"requires_cookie": True},
    }), encoding="utf-8")
    merged = {}
    t._merge_cookie_requirement_file(str(f), merged)
    assert merged == {"100": None, "200": None, "300": True}


def test_merge_silent_on_missing_file(tmp_path):
    t = _stub(tmp_path)
    merged = {"existing": True}
    t._merge_cookie_requirement_file(str(tmp_path / "does_not_exist.json"), merged)
    assert merged == {"existing": True}


def test_merge_silent_on_invalid_json(tmp_path):
    t = _stub(tmp_path)
    f = tmp_path / "garbage.json"
    f.write_text("not json", encoding="utf-8")
    merged = {"existing": False}
    t._merge_cookie_requirement_file(str(f), merged)
    assert merged == {"existing": False}


def test_merge_silent_on_top_level_non_dict_payload(tmp_path):
    t = _stub(tmp_path)
    f = tmp_path / "in.json"
    f.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    merged = {}
    t._merge_cookie_requirement_file(str(f), merged)
    assert merged == {}


# ── _load_saved_cookie_requirement_map (end-to-end) ─────────────────────────

def test_load_merges_primary_and_history(tmp_path):
    """A primary file's value beats history; history fills gaps."""
    t = _stub(tmp_path)
    primary = tmp_path / "pixiv_cookie_requirement.json"
    primary.write_text(json.dumps({
        "100": {"requires_cookie": True},
    }), encoding="utf-8")

    hist = tmp_path / "history"
    hist.mkdir()
    (hist / "pixiv_cookie_requirement.json.20260101").write_text(json.dumps({
        "100": {"requires_cookie": False},  # MUST be ignored — primary wins
        "200": {"requires_cookie": True},   # gap-fill
    }), encoding="utf-8")

    merged = t._load_saved_cookie_requirement_map()
    assert merged["100"] is True
    assert merged["200"] is True
