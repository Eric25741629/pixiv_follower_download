"""Behavior tests for download_thread._build_download_filename (Phase 22)."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread

_build = download_thread._build_download_filename

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
