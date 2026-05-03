"""Tests for _apply_legacy_constructor_args and its sub-helpers.

The legacy-args parser preserves backward compat with old call sites that
pass positional or keyword args from before the constructor signature
stabilized. Tests verify each shape and that malformed inputs are
silently skipped (the parser must NEVER raise — that would break __init__).
"""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


# ── _cast_or_skip ───────────────────────────────────────────────────────────

def test_cast_or_skip_returns_value_on_success():
    assert download_thread._cast_or_skip(int, "42") == 42
    assert download_thread._cast_or_skip(bool, 1) is True
    assert download_thread._cast_or_skip(str, 123) == "123"


def test_cast_or_skip_returns_none_on_caster_exception():
    assert download_thread._cast_or_skip(int, "not-a-number") is None


def test_cast_or_skip_returns_none_when_caster_returns_none():
    """Used by lambdas like `lambda v: str(v).strip() or None` to skip empty inputs."""
    assert download_thread._cast_or_skip(lambda v: str(v).strip() or None, "  ") is None


# ── _apply_legacy_positional ────────────────────────────────────────────────

def test_positional_fills_overrides_in_order():
    overrides = {}
    download_thread._apply_legacy_positional(
        (True, "/path/to/cjxl.exe", False, 9), overrides
    )
    assert overrides == {
        "jxl_enable": True,
        "jxl_cjxl_path": "/path/to/cjxl.exe",
        "jxl_delete_original": False,
        "jxl_effort": 9,
    }


def test_positional_handles_partial_args():
    overrides = {}
    download_thread._apply_legacy_positional((True, "/p"), overrides)
    assert overrides == {"jxl_enable": True, "jxl_cjxl_path": "/p"}


def test_positional_no_args_is_noop():
    overrides = {}
    download_thread._apply_legacy_positional((), overrides)
    assert overrides == {}
    download_thread._apply_legacy_positional(None, overrides)
    assert overrides == {}


def test_positional_skips_bad_caster_values():
    """An int(value) that raises must be silently dropped, not crash."""
    overrides = {}
    # 4th positional is int — pass garbage; first 3 still apply.
    download_thread._apply_legacy_positional((True, "/p", False, "abc"), overrides)
    assert "jxl_effort" not in overrides
    assert overrides["jxl_enable"] is True


def test_positional_skips_blank_path():
    overrides = {}
    # Blank string for jxl_cjxl_path → caster returns None → skipped.
    download_thread._apply_legacy_positional((True, "  ", False, 7), overrides)
    assert "jxl_cjxl_path" not in overrides
    assert overrides["jxl_enable"] is True
    assert overrides["jxl_effort"] == 7


# ── _apply_legacy_scalar_kwargs ─────────────────────────────────────────────

def test_scalar_kwargs_apply_all_known_keys():
    overrides = {}
    download_thread._apply_legacy_scalar_kwargs({
        "jxl_enable": True,
        "jxl_cjxl_path": "/p",
        "jxl_delete_original": False,
        "jxl_effort": 5,
        "like_num": 100,
        "ai_gen_dir": True,
        "filename_template": "{pid}_{page}.{ext}",
    }, overrides)
    assert overrides == {
        "jxl_enable": True,
        "jxl_cjxl_path": "/p",
        "jxl_delete_original": False,
        "jxl_effort": 5,
        "like_num": 100,
        "ai_gen_dir": True,
        "filename_template": "{pid}_{page}.{ext}",
    }


def test_scalar_kwargs_skip_unknown_keys():
    overrides = {}
    download_thread._apply_legacy_scalar_kwargs({"unrelated": "ignore me"}, overrides)
    assert overrides == {}


def test_scalar_kwargs_skip_caster_failures():
    overrides = {}
    download_thread._apply_legacy_scalar_kwargs({"jxl_effort": "garbage"}, overrides)
    assert "jxl_effort" not in overrides


def test_scalar_kwargs_skip_blank_filename_template():
    """Whitespace-only template → caster returns None → caller will use the default."""
    overrides = {}
    download_thread._apply_legacy_scalar_kwargs({"filename_template": "   "}, overrides)
    assert "filename_template" not in overrides


# ── _apply_legacy_list_kwargs ───────────────────────────────────────────────

def test_list_kwargs_pass_through_actual_lists():
    overrides = {}
    download_thread._apply_legacy_list_kwargs({
        "ban_tag": ["adult"],
        "must_tag": ["female"],
    }, overrides)
    assert overrides == {"ban_tag": ["adult"], "must_tag": ["female"]}


def test_list_kwargs_skip_non_list_values():
    overrides = {}
    download_thread._apply_legacy_list_kwargs({
        "ban_tag": "not-a-list",
        "must_tag": None,
    }, overrides)
    assert overrides == {}


def test_list_kwargs_no_keys_is_noop():
    overrides = {}
    download_thread._apply_legacy_list_kwargs({}, overrides)
    assert overrides == {}


# ── _apply_legacy_constructor_args (full integration) ───────────────────────

def test_full_pipeline_combines_all_shapes():
    overrides = download_thread._apply_legacy_constructor_args(
        (True, "/jxl"),
        {"like_num": 50, "ban_tag": ["a", "b"], "ai_gen_dir": True},
    )
    assert overrides["jxl_enable"] is True
    assert overrides["jxl_cjxl_path"] == "/jxl"
    assert overrides["like_num"] == 50
    assert overrides["ban_tag"] == ["a", "b"]
    assert overrides["ai_gen_dir"] is True


def test_full_pipeline_no_inputs_returns_empty():
    assert download_thread._apply_legacy_constructor_args((), {}) == {}
    assert download_thread._apply_legacy_constructor_args(None, None) == {}


def test_full_pipeline_garbage_inputs_dont_raise():
    """Defense-in-depth: __init__ MUST never crash because of legacy args."""
    overrides = download_thread._apply_legacy_constructor_args(
        ("garbage", object(), None, "abc"),
        {"jxl_effort": "x", "ban_tag": 42, "special_like_rules": object()},
    )
    assert isinstance(overrides, dict)
