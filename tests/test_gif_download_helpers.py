"""Tests for the helpers extracted from download_thread.gif_download."""
from pathlib import Path
import datetime
import io
import sys
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


def _stub(tmp_path):
    t = download_thread.__new__(download_thread)
    t.download_path = str(tmp_path)
    t.create_dir = False
    t.no_R18G_dir = False
    t.no_R18_dir = False
    t.ai_gen_dir = False
    t.notag = False
    t.notime = False
    t._stats_collector = None
    return t


# ── _extract_ugoira_frame_blobs ──────────────────────────────────────────────

def _make_zip_bytes(entries):
    """entries: list of (name, content_bytes); returns ZIP bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in entries:
            z.writestr(name, content)
    return buf.getvalue()


def test_extract_frame_blobs_in_archive_order(tmp_path):
    t = _stub(tmp_path)
    src = _make_zip_bytes([
        ("000000.jpg", b"frame0"),
        ("000001.jpg", b"frame1"),
        ("000002.jpg", b"frame2"),
    ])
    out = t._extract_ugoira_frame_blobs(src)
    assert out == [b"frame0", b"frame1", b"frame2"]


def test_extract_frame_blobs_skips_directory_entries(tmp_path):
    t = _stub(tmp_path)
    # zipfile.namelist() will include a "dir/" entry written explicitly
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("dir/", b"")
        z.writestr("dir/000000.jpg", b"actual")
    out = t._extract_ugoira_frame_blobs(buf.getvalue())
    assert out == [b"actual"]


def test_extract_frame_blobs_empty_archive(tmp_path):
    t = _stub(tmp_path)
    out = t._extract_ugoira_frame_blobs(_make_zip_bytes([]))
    assert out == []


# ── _build_ugoira_save_path ──────────────────────────────────────────────────

def test_build_ugoira_save_path_creates_target_dir(tmp_path):
    t = _stub(tmp_path)
    my_time = datetime.datetime(2026, 5, 1, 12, 30, 45)
    path = t._build_ugoira_save_path("123456", ["オリジナル"], my_time)
    # Must end with .gif and live under the download root
    assert path.endswith(".gif")
    assert str(tmp_path) in path


def test_build_ugoira_save_path_routes_r18_to_subfolder(tmp_path):
    t = _stub(tmp_path)
    my_time = datetime.datetime(2026, 5, 1, 12, 30, 45)
    path = t._build_ugoira_save_path("123456", ["R-18"], my_time)
    # Should land in GIF/R-18 subfolder
    p = Path(path)
    assert p.parent.name == "R-18"
    assert p.parent.parent.name == "GIF"


def test_build_ugoira_save_path_routes_r18g_to_subfolder(tmp_path):
    t = _stub(tmp_path)
    my_time = datetime.datetime(2026, 5, 1, 12, 30, 45)
    path = t._build_ugoira_save_path("123456", ["R-18G"], my_time)
    p = Path(path)
    assert p.parent.name == "R-18G"


def test_build_ugoira_save_path_fallback_name_when_filename_helper_breaks(tmp_path):
    t = _stub(tmp_path)
    # Force the filename helper path to fail by removing a method it needs
    t._build_hashtag_text = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    my_time = datetime.datetime(2026, 5, 1, 12, 30, 45)
    path = t._build_ugoira_save_path("99999", ["safe"], my_time)
    # Fallback name format: 'illust_<pid>_<YYYYMMDD_HHMMSS>.gif'
    assert "illust_99999" in Path(path).name
    assert path.endswith(".gif")
