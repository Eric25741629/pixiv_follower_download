"""_jpg_attempt integrity gate: a truncated body (header OK, footer missing) is
removed and raised so the existing retry loop re-downloads; a valid body passes.

This is the wiring that delivers the user's requirement ("if an image didn't
finish downloading — check header and footer — re-download"). The pure
validator itself is covered by tests/test_image_integrity.py; this proves it is
actually invoked inside the download path and gates the result correctly.
"""
from pathlib import Path
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from app.core.thread_download import download_thread

JPEG_OK = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\x10" * 64 + b"\xff\xd9"
JPEG_TRUNC = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # header only, no footer


class _Resp:
    status_code = 200

    def raise_for_status(self):
        pass


class _Http:
    def get(self, *a, **k):
        return _Resp()


def _make_thread(tmp_path, body):
    t = download_thread.__new__(download_thread)
    t._stop_event = threading.Event()
    t._stats_collector = None
    t._pid_cookie_used = {}
    t.cookie_pool = []
    t._cookie_alias_map = {}
    t._metadata_db = None
    t._resolve_pid_and_cookie = lambda url, source=None: ("12345", None, False)
    t._load_artwork_metadata = lambda pid, cookie: (["safe"], 100, 1, "tmpl")
    t._jpg_build_headers = lambda *a, **k: {}
    t._jpg_resolve_filename = lambda *a, **k: "out.jpg"
    t._resolve_download_target_dir = lambda *a, **k: str(tmp_path)
    t._apply_download_mtime = lambda *a, **k: None
    t._enqueue_jxl = lambda *a, **k: None

    def _fake_stream(htmlfile, filepath):
        with open(filepath, "wb") as f:
            f.write(body)
        return len(body)

    t._jpg_stream_to_disk = _fake_stream
    return t


def test_jpg_attempt_rejects_truncated_download(tmp_path):
    t = _make_thread(tmp_path, JPEG_TRUNC)
    with pytest.raises(ValueError):
        t._jpg_attempt("https://i.pximg.net/12345_p0.jpg", _Http(), "20260101_000000")
    assert not (tmp_path / "out.jpg").exists()  # truncated file removed for retry


def test_jpg_attempt_accepts_valid_download(tmp_path):
    t = _make_thread(tmp_path, JPEG_OK)
    rc = t._jpg_attempt("https://i.pximg.net/12345_p0.jpg", _Http(), "20260101_000000")
    assert rc == 0
    assert (tmp_path / "out.jpg").exists()
