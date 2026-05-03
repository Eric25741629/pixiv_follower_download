"""Tests for the helpers extracted from download_thread.jpg_download."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


# ── _jpg_extract_page_and_format (static, pure) ─────────────────────────────

def test_extract_page_and_format_jpg():
    page, ext = download_thread._jpg_extract_page_and_format(
        "https://i.pximg.net/img-original/img/.../12345_p0.jpg"
    )
    assert page == "p0"
    assert ext == "jpg"


def test_extract_page_and_format_png_higher_page():
    page, ext = download_thread._jpg_extract_page_and_format(
        "https://i.pximg.net/img-original/img/.../12345_p7.png"
    )
    assert page == "p7"
    assert ext == "png"


def test_extract_page_and_format_double_digit_page():
    page, ext = download_thread._jpg_extract_page_and_format(
        "https://i.pximg.net/img-original/img/.../12345_p12.jpg"
    )
    assert page == "p12"
    assert ext == "jpg"


# ── _jpg_resolve_filename (instance method) ─────────────────────────────────

def test_jpg_resolve_filename_default_layout(tmp_path):
    t = download_thread.__new__(download_thread)
    t.notag = True
    t.notime = False
    t.filename_template = ""
    t._build_hashtag_text = lambda tag, max_len: " "
    name = t._jpg_resolve_filename("99999", "p0", "png", ["safe"], "20260101_120000")
    assert "99999" in name
    assert name.endswith(".png")


def test_jpg_resolve_filename_with_template(tmp_path):
    t = download_thread.__new__(download_thread)
    t.notag = False
    t.notime = False
    t.filename_template = "{pid}_{page}.{ext}"
    t._build_hashtag_text = lambda tag, max_len: "  art"
    name = t._jpg_resolve_filename("99999", "p0", "jpg", ["art"], "20260101_120000")
    assert name == "99999_p0.jpg"


def test_jpg_resolve_filename_falls_back_when_helpers_raise(tmp_path):
    """If _build_download_filename's hashtag helper raises, fall back to a safe default."""
    t = download_thread.__new__(download_thread)
    t.notag = False
    t.notime = False
    t.filename_template = ""
    t._build_hashtag_text = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    name = t._jpg_resolve_filename("12345", "p0", "png", ["x"], "TS")
    assert name == "illust_12345p0TS.png"


# ── _jpg_stream_to_disk (instance method) ───────────────────────────────────

class _FakeStreamResponse:
    def __init__(self, chunks):
        self.chunks = chunks

    def iter_content(self, chunk_size=None):
        return iter(self.chunks)


def test_jpg_stream_to_disk_writes_all_chunks(tmp_path):
    t = download_thread.__new__(download_thread)
    resp = _FakeStreamResponse([b"abc", b"de", b"fg"])
    out = tmp_path / "out.jpg"
    size = t._jpg_stream_to_disk(resp, str(out))
    assert size == 7
    assert out.read_bytes() == b"abcdefg"


def test_jpg_stream_to_disk_handles_empty_response(tmp_path):
    t = download_thread.__new__(download_thread)
    resp = _FakeStreamResponse([])
    out = tmp_path / "empty.jpg"
    size = t._jpg_stream_to_disk(resp, str(out))
    assert size == 0
    assert out.exists()
    assert out.read_bytes() == b""


# ── _jpg_build_headers ──────────────────────────────────────────────────────

def test_jpg_build_headers_uses_jpg_user_agent(tmp_path):
    t = download_thread.__new__(download_thread)
    # Stub _build_artwork_headers to return a baseline dict
    t._build_artwork_headers = lambda pid, cookie, need_cookie: {
        "Referer": "https://www.pixiv.net/",
        "User-Agent": "OLD_UA",
    }
    headers = t._jpg_build_headers("99", None, False)
    # Per the original logic, jpg overrides UA to a Chrome 99 string
    assert headers["User-Agent"] == download_thread._JPG_USER_AGENT
    assert "Chrome/99" in headers["User-Agent"]
    # Other headers carried through
    assert headers["Referer"] == "https://www.pixiv.net/"
