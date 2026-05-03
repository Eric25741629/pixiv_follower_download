"""Tests for app.core.filename_utils."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.filename_utils import (
    DEFAULT_MAX_FILENAME_LEN,
    render_filename_template,
    sanitize_filename,
)


# ── sanitize_filename ───────────────────────────────────────────────────────

def test_passes_normal_filename():
    assert sanitize_filename("normal_name.jpg") == "normal_name.jpg"


def test_replaces_windows_forbidden_chars():
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j.jpg') == "a_b_c_d_e_f_g_h_i_j.jpg"


def test_replaces_control_chars():
    out = sanitize_filename("a\x00b\x01c.jpg")
    # Control chars become "_"
    assert out == "a_b_c.jpg"


def test_strips_trailing_dot_and_space():
    """NTFS silently strips trailing dots and spaces — pre-empt the surprise."""
    assert sanitize_filename("name.  ") == "name"
    assert sanitize_filename("name...") == "name"
    assert sanitize_filename("  name.jpg  ") == "name.jpg"


def test_blank_input_returns_underscore():
    assert sanitize_filename("") == "_"
    assert sanitize_filename("   ") == "_"
    assert sanitize_filename("...") == "_"
    assert sanitize_filename(None) == "_"


def test_handles_chinese_japanese_characters():
    """Non-ASCII printable characters must pass through unchanged."""
    assert sanitize_filename("オリジナル_作品.jpg") == "オリジナル_作品.jpg"
    assert sanitize_filename("中文檔名.png") == "中文檔名.png"


def test_truncates_long_stem_preserving_extension():
    long_name = "a" * 500 + ".jpg"
    out = sanitize_filename(long_name, max_len=50)
    assert len(out) == 50
    assert out.endswith(".jpg")
    assert out.startswith("a")


def test_truncate_default_max_len_is_200():
    long_name = "x" * 300 + ".png"
    out = sanitize_filename(long_name)
    assert len(out) == DEFAULT_MAX_FILENAME_LEN
    assert out.endswith(".png")


def test_truncated_stem_strips_trailing_dot_and_space():
    """If the stem ends with a dot or space after truncation, strip it again."""
    name = "abc.def . " + "x" * 200
    # max_len=10, stem must be safe at boundary
    out = sanitize_filename(name, max_len=10)
    # Expect no trailing dot or space before the original extension
    assert not out.split(".")[0].endswith(".")
    assert not out.split(".")[0].endswith(" ")


def test_reserved_device_name_prefixed():
    """CON.txt → _CON.txt so Windows doesn't open the printer device."""
    assert sanitize_filename("CON.txt") == "_CON.txt"
    assert sanitize_filename("nul.log") == "_nul.log"
    assert sanitize_filename("COM1.bin") == "_COM1.bin"
    # Case-insensitive matching but original case preserved
    assert sanitize_filename("Aux.dat") == "_Aux.dat"


def test_reserved_device_name_only_matches_full_stem():
    """'CONsole.txt' should NOT be prefixed — only exact device names trigger."""
    assert sanitize_filename("CONsole.txt") == "CONsole.txt"


def test_extension_longer_than_max_len_falls_back():
    """Pathological case where the extension itself overflows the budget."""
    name = "a." + "b" * 50
    out = sanitize_filename(name, max_len=10)
    assert len(out) <= 10
    assert out  # not empty


def test_replacement_char_is_configurable():
    out = sanitize_filename("a/b\\c", replacement="-")
    assert out == "a-b-c"


# ── render_filename_template ────────────────────────────────────────────────

def test_render_substitutes_known_placeholders():
    out = render_filename_template(
        "{pid}_{page}_{ext}",
        {"pid": "12345", "page": "p0", "ext": "png"},
    )
    assert out == "12345_p0_png"


def test_render_preserves_literal_text():
    out = render_filename_template(
        "PID_{pid}_part_{page}.{ext}",
        {"pid": "1", "page": "0", "ext": "jpg"},
    )
    assert out == "PID_1_part_0.jpg"


def test_render_leaves_unknown_placeholders_untouched():
    """Typo'd placeholders remain visible so the user notices, not crash."""
    out = render_filename_template(
        "{pid}_{typo_key}",
        {"pid": "12345"},
    )
    assert out == "12345_{typo_key}"


def test_render_treats_none_value_as_empty():
    out = render_filename_template("{pid}_{tag}", {"pid": "1", "tag": None})
    assert out == "1_"


def test_render_empty_template_returns_empty_string():
    assert render_filename_template("", {"pid": "1"}) == ""
    assert render_filename_template(None, {"pid": "1"}) == ""


def test_render_with_no_placeholders():
    assert render_filename_template("static_name", {}) == "static_name"


def test_render_handles_repeated_placeholder():
    out = render_filename_template("{pid}_{pid}", {"pid": "42"})
    assert out == "42_42"
