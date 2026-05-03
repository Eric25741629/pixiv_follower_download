"""Behavior tests for download_thread._build_download_filename (Phase 22)."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


def _build(*args, **kwargs):
    """Adapter so existing tests don't have to mention the new template kwarg."""
    return download_thread._build_download_filename(*args, **kwargs)

# Emulate _build_hashtag_text output ("  tag1 tag2" or " " when empty).
HASHTAG_WITH = "  art cg"
HASHTAG_EMPTY = " "
TIMETAG = "20260101_120000"


# ── GIF layout (no page suffix) ─────────────────────────────────────────────

def test_gif_with_time_no_tag():
    # notime=False, notag=True, page_suffix=""
    assert _build("12345", page_suffix="", ext="gif", hashtag=HASHTAG_EMPTY,
                  timetag=TIMETAG, notag=True, notime=False) == \
           TIMETAG + "_PID12345.gif"


def test_gif_with_time_with_tag():
    assert _build("12345", page_suffix="", ext="gif", hashtag=HASHTAG_WITH,
                  timetag=TIMETAG, notag=False, notime=False) == \
           TIMETAG + "_PID12345" + HASHTAG_WITH + ".gif"


def test_gif_no_time_no_tag():
    assert _build("12345", page_suffix="", ext="gif", hashtag=HASHTAG_EMPTY,
                  timetag=TIMETAG, notag=True, notime=True) == "PID12345.gif"


def test_gif_no_time_with_tag():
    assert _build("12345", page_suffix="", ext="gif", hashtag=HASHTAG_WITH,
                  timetag=TIMETAG, notag=False, notime=True) == \
           "PID12345" + HASHTAG_WITH + ".gif"


# ── JPG layout (with page suffix) ────────────────────────────────────────────

def test_jpg_with_time_with_tag():
    assert _build("67890", page_suffix="p0", ext="jpg", hashtag=HASHTAG_WITH,
                  timetag=TIMETAG, notag=False, notime=False) == \
           TIMETAG + "_PID67890p0" + HASHTAG_WITH + ".jpg"


def test_jpg_with_time_no_tag():
    assert _build("67890", page_suffix="p0", ext="jpg", hashtag=HASHTAG_EMPTY,
                  timetag=TIMETAG, notag=True, notime=False) == \
           TIMETAG + "_PID67890p0.jpg"


def test_jpg_no_time_with_tag():
    assert _build("67890", page_suffix="p1", ext="png", hashtag=HASHTAG_WITH,
                  timetag=TIMETAG, notag=False, notime=True) == \
           "PID67890p1" + HASHTAG_WITH + ".png"


def test_jpg_no_time_no_tag():
    assert _build("67890", page_suffix="p0", ext="png", hashtag=HASHTAG_EMPTY,
                  timetag=TIMETAG, notag=True, notime=True) == \
           "PID67890p0.png"


# ── filename_template (user-supplied override) ──────────────────────────────

def test_template_basic_substitution():
    out = _build("12345", page_suffix="p0", ext="png", hashtag="  art",
                 timetag=TIMETAG, notag=False, notime=False,
                 template="{pid}_{page}.{ext}")
    assert out == "12345_p0.png"


def test_template_appends_extension_when_missing():
    """If the template doesn't end with the right extension, we append it."""
    out = _build("12345", page_suffix="p0", ext="jpg", hashtag=" ",
                 timetag=TIMETAG, notag=True, notime=True,
                 template="{pid}_{page}")
    assert out == "12345_p0.jpg"


def test_template_does_not_double_extension():
    """If the template already ends with .{ext}, don't append a second one."""
    out = _build("12345", page_suffix="p0", ext="png", hashtag=" ",
                 timetag=TIMETAG, notag=True, notime=True,
                 template="{pid}_{page}.{ext}")
    assert out == "12345_p0.png"
    assert not out.endswith(".png.png")


def test_template_notag_blanks_hashtag_placeholder():
    out = _build("99", page_suffix="p1", ext="jpg", hashtag="  art",
                 timetag=TIMETAG, notag=True, notime=False,
                 template="{pid}{hashtag}.{ext}")
    # hashtag suppressed by notag=True
    assert out == "99.jpg"


def test_template_notime_blanks_timetag_placeholder():
    out = _build("99", page_suffix="p0", ext="png", hashtag=" ",
                 timetag=TIMETAG, notag=True, notime=True,
                 template="{timetag}_{pid}.{ext}")
    # leading "_" plus pid (timetag empty)
    assert out.endswith("99.png")


def test_template_unknown_placeholder_left_as_is():
    out = _build("1", page_suffix="", ext="jpg", hashtag=" ",
                 timetag=TIMETAG, notag=True, notime=True,
                 template="{pid}_{typo_key}")
    assert "{typo_key}" in out
    assert out.startswith("1_")


def test_template_sanitizes_forbidden_chars():
    """Even if the user template injects invalid characters (or substitutions
    contain them), the result must be filesystem-safe."""
    out = _build("1", page_suffix="", ext="png", hashtag="<bad>",
                 timetag=TIMETAG, notag=False, notime=True,
                 template="{pid}_{hashtag}.{ext}")
    # No raw <, > or surrounding angle brackets remain
    assert "<" not in out
    assert ">" not in out


def test_template_empty_falls_back_to_default():
    out = _build("12345", page_suffix="p0", ext="png", hashtag=" ",
                 timetag=TIMETAG, notag=True, notime=True,
                 template="")
    assert out == "PID12345p0.png"


def test_template_whitespace_only_falls_back_to_default():
    out = _build("12345", page_suffix="p0", ext="png", hashtag=" ",
                 timetag=TIMETAG, notag=True, notime=True,
                 template="   ")
    assert out == "PID12345p0.png"


def test_template_date_and_time_placeholders():
    out = _build("1", page_suffix="", ext="jpg", hashtag=" ",
                 timetag="20260102_153045", notag=True, notime=False,
                 template="{date}-{time}-{pid}.{ext}")
    assert out == "20260102-153045-1.jpg"
