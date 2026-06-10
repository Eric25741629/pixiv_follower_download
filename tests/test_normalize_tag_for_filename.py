"""Tests for ``download_thread._normalize_tag_for_filename`` after the
tag_strip_brackets / tag_strip_special_chars / whitespace-unification
extensions.

We instantiate a minimal ``download_thread`` skeleton (no threading, no IO)
that just carries the two boolean attributes — the helper only depends on
those two flags and the static regexes."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


def _make(brackets=False, special=False):
    t = download_thread.__new__(download_thread)
    t.tag_strip_brackets = brackets
    t.tag_strip_special_chars = special
    return t


# ── always-on whitespace unification ──────────────────────────────────────

def test_zero_width_chars_are_stripped_unconditionally():
    """Even with both toggles off, zero-width chars must vanish — they have
    no display width and only consume filename budget."""
    t = _make()
    # tag with: ZWSP, ZWNJ, ZWJ, BOM, soft hyphen interleaved with text
    out = t._normalize_tag_for_filename("a​b‌c‍d﻿e­f")
    assert out == "abcdef"


def test_full_width_space_collapses_to_ascii_space():
    """U+3000 full-width space must become a single ASCII space."""
    t = _make()
    assert t._normalize_tag_for_filename("ホロライブ　hololive") == "ホロライブ hololive"


def test_no_break_space_collapses_to_ascii_space():
    t = _make()
    assert t._normalize_tag_for_filename("foo bar") == "foo bar"


def test_mixed_whitespace_runs_collapse_to_single_space():
    t = _make()
    assert t._normalize_tag_for_filename("a  \t　  b") == "a b"


# ── brackets toggle ────────────────────────────────────────────────────────

def test_brackets_off_keeps_content():
    t = _make(brackets=False)
    assert t._normalize_tag_for_filename("ホロライブ（hololive）") == "ホロライブ（hololive）"


def test_brackets_on_removes_halfwidth_parens():
    t = _make(brackets=True)
    assert t._normalize_tag_for_filename("ホロライブ(hololive)") == "ホロライブ"


def test_brackets_on_removes_fullwidth_parens():
    t = _make(brackets=True)
    assert t._normalize_tag_for_filename("ホロライブ（hololive）") == "ホロライブ"


def test_brackets_on_removes_square_brackets():
    t = _make(brackets=True)
    assert t._normalize_tag_for_filename("R-18[警告]") == "R-18"


def test_brackets_on_removes_cjk_brackets():
    t = _make(brackets=True)
    for pair in [
        ("【", "】"),
        ("《", "》"),
        ("〈", "〉"),
        ("「", "」"),
        ("『", "』"),
        ("〔", "〕"),
        ("〘", "〙"),
    ]:
        sample = f"core{pair[0]}内容{pair[1]}"
        assert t._normalize_tag_for_filename(sample) == "core", (
            f"failed for bracket pair {pair}: got "
            f"{t._normalize_tag_for_filename(sample)!r}"
        )


def test_brackets_on_handles_nested_brackets():
    t = _make(brackets=True)
    # Inner pair removed first, then outer pair becomes empty and is mopped up.
    assert t._normalize_tag_for_filename("a((b)(c))d") == "ad"


def test_brackets_on_preserves_unmatched_single_side():
    """Single-side unmatched bracket survives — `R-18(警告` stays readable
    rather than getting mangled by greedy regex."""
    t = _make(brackets=True)
    assert t._normalize_tag_for_filename("R-18(警告") == "R-18(警告"
    assert t._normalize_tag_for_filename("警告)R-18") == "警告)R-18"


def test_brackets_on_keeps_inter_bracket_text():
    """A tag like `prefix(a)middle(b)suffix` keeps `prefixmiddlesuffix`."""
    t = _make(brackets=True)
    assert t._normalize_tag_for_filename("prefix(a)middle(b)suffix") == "prefixmiddlesuffix"


# ── special chars toggle ───────────────────────────────────────────────────

def test_special_chars_off_keeps_decorations():
    t = _make(special=False)
    assert t._normalize_tag_for_filename("R-18★☆") == "R-18★☆"


def test_special_chars_on_strips_misc_symbols():
    t = _make(special=True)
    assert t._normalize_tag_for_filename("R-18★☆♀♂♪♫") == "R-18"


def test_special_chars_on_strips_geometric_shapes():
    t = _make(special=True)
    assert t._normalize_tag_for_filename("tag◯●◎◇◆") == "tag"


def test_special_chars_on_strips_emoji():
    """Emoji from U+1F300-U+1FAFF should all be stripped."""
    t = _make(special=True)
    samples = [
        ("可愛😀", "可愛"),                                # U+1F600 emoticon
        ("猫🐱tag", "cattag" if False else "猫tag"),      # U+1F431
        ("rocket🚀go", "rocketgo"),                         # U+1F680
        ("flag🏳", "flag"),                                  # U+1F3F3
        ("party🎉🎊", "party"),                              # 1F389/1F38A
        ("kiss💋mark", "kissmark"),                          # U+1F48B
        ("rainbow🌈", "rainbow"),                            # U+1F308
    ]
    for raw, expected in samples:
        assert t._normalize_tag_for_filename(raw) == expected, (
            f"raw={raw!r} got={t._normalize_tag_for_filename(raw)!r}"
        )


def test_special_chars_on_strips_emoji_variation_selectors():
    """Variation selectors (U+FE00-FE0F, especially VS16 U+FE0F) combine with
    a base emoji to switch the render style. If we strip the emoji but leave
    VS16 behind, the filename carries an invisible codepoint. Strip them
    together."""
    t = _make(special=True)
    # ❤️ = U+2764 + U+FE0F; ⭐️ = U+2B50 + U+FE0F
    assert t._normalize_tag_for_filename("love❤️tag") == "lovetag"
    assert t._normalize_tag_for_filename("a♪️b") == "ab"
    # bare VS16 with no preceding emoji should also be stripped
    assert t._normalize_tag_for_filename("plain️") == "plain"


def test_special_chars_on_strips_arrows_and_dingbats():
    t = _make(special=True)
    assert t._normalize_tag_for_filename("←→↑↓foo") == "foo"
    assert t._normalize_tag_for_filename("bar✂✈✏") == "bar"


def test_special_chars_on_strips_explicit_extras():
    t = _make(special=True)
    assert t._normalize_tag_for_filename("a※b〝c〞d〽e") == "abcde"


def test_special_chars_on_does_not_strip_brackets():
    """special_chars toggle alone must NOT touch bracket characters — those
    belong to the brackets toggle. If a user turns special_chars on but
    brackets off, brackets and their content must survive."""
    t = _make(brackets=False, special=True)
    # 【 】 are in the CJK Symbols and Punctuation block (U+3010-U+3011)
    # which we deliberately excluded from the decorative-chars regex.
    assert t._normalize_tag_for_filename("R-18【警告】") == "R-18【警告】"
    assert t._normalize_tag_for_filename("ホロライブ（hololive）") == "ホロライブ（hololive）"


def test_special_chars_on_preserves_basic_ascii_punctuation():
    t = _make(special=True)
    assert t._normalize_tag_for_filename("R-18_test.v2") == "R-18_test.v2"
    assert t._normalize_tag_for_filename("a-b_c.d!e?f") == "a-b_c.d!e?f"


def test_special_chars_on_preserves_cjk_letters():
    t = _make(special=True)
    assert t._normalize_tag_for_filename("ホロライブ猫耳") == "ホロライブ猫耳"
    assert t._normalize_tag_for_filename("漢字tag") == "漢字tag"


# ── combined toggles ──────────────────────────────────────────────────────

def test_both_toggles_on_combined_cleanup():
    t = _make(brackets=True, special=True)
    raw = "ホロライブ（hololive）★ vs 猫耳【翻譯】♀ vs rocket🚀"
    out = t._normalize_tag_for_filename(raw)
    # brackets+content gone, decorative gone, emoji gone, spaces normalized
    assert out == "ホロライブ vs 猫耳 vs rocket"


def test_both_toggles_off_matches_legacy_behavior():
    """With both toggles off the only deltas from raw input should be
    zero-width stripping + whitespace collapse + empty-bracket cleanup +
    leading/trailing trim. Nothing else should change."""
    t = _make(brackets=False, special=False)
    assert t._normalize_tag_for_filename("R-18[警告]") == "R-18[警告]"
    assert t._normalize_tag_for_filename("a()b") == "ab"  # legacy empty-bracket cleanup
    assert t._normalize_tag_for_filename("  hello  ") == "hello"
