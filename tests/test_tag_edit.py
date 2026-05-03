"""Tests for tag_edit.Tag and its extracted helpers."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.tag_edit import (
    Tag,
    _BUCKET_ALWAYS,
    _BUCKET_GT5LTE10,
    _BUCKET_GTE20,
    _BUCKET_LTE5,
    _bucket_for_length,
)


# ── _bucket_for_length (static, pure) ──────────────────────────────────────

def test_bucket_for_length_lte5():
    assert _bucket_for_length(0) is _BUCKET_LTE5
    assert _bucket_for_length(5) is _BUCKET_LTE5


def test_bucket_for_length_6_to_10():
    assert _bucket_for_length(6) is _BUCKET_GT5LTE10
    assert _bucket_for_length(10) is _BUCKET_GT5LTE10


def test_bucket_for_length_11_to_19():
    from app.core.tag_edit import _BUCKET_GT10LT20
    assert _bucket_for_length(11) is _BUCKET_GT10LT20
    assert _bucket_for_length(19) is _BUCKET_GT10LT20


def test_bucket_for_length_20_plus():
    assert _bucket_for_length(20) is _BUCKET_GTE20
    assert _bucket_for_length(100) is _BUCKET_GTE20


# ── Tag (the public function) ──────────────────────────────────────────────

def test_tag_filters_users_iri_tags():
    """Tags containing the 'users入り' marker are bookmark-bucket tags from Pixiv
    (e.g., '5000users入り') — they're not real tags so we drop them. Use a
    nonsense tag that tag_rules.json won't translate, so the assertion stays
    stable across rule updates."""
    sentinel = "zzzunknown_tag_xyz"
    out = Tag(["5000users入り", sentinel, "100users入り"])
    assert sentinel in out
    assert not any("users入り" in t for t in out)


def test_tag_strips_forbidden_chars():
    """Filename-forbidden chars (and some Pixiv-specific ones) are stripped."""
    out = Tag(['hello"world', "test:case", "a/b\\c", "x*y?z"])
    # Forbidden chars are gone. Use substring checks since rule replacements
    # may further transform short stems.
    for t in out:
        for ch in '"}{[]:<|>?/※\\⊂⊃*':
            assert ch not in t


def test_tag_replaces_period_and_middle_dot_with_underscore():
    out = Tag(["zzunknown.tag", "zzother・tag"])
    assert out[0].replace("zz", "").replace("unknown", "").replace("tag", "") == "_"
    assert "・" not in out[1]
    assert "_" in out[1]


def test_tag_handles_empty_input():
    assert Tag([]) == []


def test_tag_returns_a_list():
    """Even with arbitrary input the function returns a list, never None or raises."""
    out = Tag(["safe_tag"])
    assert isinstance(out, list)


def test_tag_drops_users_iri_anywhere_in_tag():
    """The filter is substring-based, not exact-match."""
    out = Tag(["プレフィックス500users入りスフィックス"])
    assert out == []
