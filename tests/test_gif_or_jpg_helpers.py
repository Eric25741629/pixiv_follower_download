"""Tests for the helpers extracted from download_thread.gif_or_jpg."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


# ── _extract_pid_from_pximg_url (static, pure) ──────────────────────────────

def test_extract_pid_from_image_url():
    url = "https://i.pximg.net/img-original/img/.../12345_p0.jpg"
    assert download_thread._extract_pid_from_pximg_url(url, is_ugoira=False) == "12345"


def test_extract_pid_from_image_url_with_dashes():
    url = "https://i.pximg.net/img-original/img/.../99999-c476a_p0.jpg"
    pid = download_thread._extract_pid_from_pximg_url(url, is_ugoira=False)
    # The split-on-underscore strategy returns the leading "99999-c476a"
    # then normalize_pid extracts the leading digits.
    assert pid == "99999"


def test_extract_pid_from_ugoira_url():
    url = "https://i.pximg.net/img-zip-ugoira/img/.../56789_ugoira1920x1080.zip"
    assert download_thread._extract_pid_from_pximg_url(url, is_ugoira=True) == "56789"


def test_extract_pid_handles_p0_suffix_correctly():
    """p0 should not bleed into the extracted PID."""
    url = "https://i.pximg.net/img-original/img/.../77777_p0.png"
    pid = download_thread._extract_pid_from_pximg_url(url, is_ugoira=False)
    assert pid == "77777"
    assert "p0" not in pid


def test_extract_pid_multiple_pages():
    url = "https://i.pximg.net/img-original/img/.../12345_p9.jpg"
    assert download_thread._extract_pid_from_pximg_url(url, is_ugoira=False) == "12345"
