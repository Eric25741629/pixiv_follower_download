"""Regression tests for the 2026-06-21 download-hang fix.

The combined/Step4 image streamer must bound a trickling/wedged transfer with a
total wall-clock deadline and honour Stop, instead of looping in iter_content
forever (the freeze that wedged the backend and left an unkillable orphan).
"""
import datetime
import os
import queue
import threading
import time

import pytest

from app.core.pixiv_thread_base import (
    DownloadDeadlineExceeded,
    DownloadStopped,
    PauseableThread,
)
from app.core.step4_media import _Step4MediaMixin


class _Worker(_Step4MediaMixin, PauseableThread):
    """Minimal concrete worker exposing the mixin + base streaming primitive."""

    def run(self):  # pragma: no cover - never started as a thread
        pass


class _FakeResp:
    def __init__(self, chunks, per_chunk_sleep=0.0):
        self._chunks = chunks
        self._sleep = per_chunk_sleep
        self.closed = False

    def iter_content(self, chunk_size=1):
        for c in self._chunks:
            if self._sleep:
                time.sleep(self._sleep)
            yield c

    def close(self):
        self.closed = True


class _TrickleResp:
    """Yields one byte forever with a tiny gap — the trickle wedge shape."""

    def __init__(self, gap=0.01):
        self._gap = gap
        self.closed = False

    def iter_content(self, chunk_size=1):
        while True:
            time.sleep(self._gap)
            yield b"x"

    def close(self):
        self.closed = True


def _mk():
    w = _Worker(queue.Queue())
    w._download_deadline_sec = 0.3
    w._stats_collector = None
    w.timelock = threading.Lock()
    w.download_time = datetime.datetime(2026, 1, 1, 0, 0, 0)
    return w


# ── _stream_to_sink ────────────────────────────────────────────────────────

def test_stream_writes_all_and_closes():
    w = _mk()
    resp = _FakeResp([b"ab", b"cd", b"ef"])
    out = bytearray()
    w._stream_to_sink(resp, out.extend, chunk_size=2, deadline_sec=5)
    assert bytes(out) == b"abcdef"
    assert resp.closed


def test_stream_stop_raises_and_closes():
    w = _mk()
    w._stop_event.set()
    resp = _FakeResp([b"a", b"b", b"c"])
    out = bytearray()
    with pytest.raises(DownloadStopped):
        w._stream_to_sink(resp, out.extend, chunk_size=1, deadline_sec=5)
    assert resp.closed


def test_stream_deadline_raises_on_trickle_and_closes():
    w = _mk()
    resp = _TrickleResp(gap=0.01)
    out = bytearray()
    start = time.monotonic()
    with pytest.raises(DownloadDeadlineExceeded):
        w._stream_to_sink(resp, out.extend, chunk_size=1, deadline_sec=0.2)
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, f"deadline did not bound the trickle (took {elapsed:.1f}s)"
    assert resp.closed


# ── _jpg_stream_to_disk (.part atomicity) ──────────────────────────────────

def test_jpg_stream_to_disk_success_no_partfile(tmp_path):
    w = _mk()
    dest = str(tmp_path / "img.jpg")
    resp = _FakeResp([b"hello ", b"world"])
    size = w._jpg_stream_to_disk(resp, dest)
    assert size == 11
    assert os.path.isfile(dest)
    with open(dest, "rb") as f:
        assert f.read() == b"hello world"
    assert not os.path.exists(dest + ".part")


def test_jpg_stream_to_disk_deadline_leaves_no_file(tmp_path):
    w = _mk()
    dest = str(tmp_path / "img.jpg")
    resp = _TrickleResp(gap=0.01)
    with pytest.raises(DownloadDeadlineExceeded):
        w._stream_to_sink_deadline = 0.2  # documentary; real budget via attr below
        w._download_deadline_sec = 0.2
        w._jpg_stream_to_disk(resp, dest)
    # No truncated file under the final name, no leftover .part.
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".part")


# ── jpg_download retry-loop interaction ────────────────────────────────────

def test_jpg_download_deadline_returns_faillist_without_retry():
    w = _mk()
    calls = []

    def fake_attempt(url, session, timetag, meta_cache=None):
        calls.append(1)
        raise DownloadDeadlineExceeded("wedged")

    w._jpg_attempt = fake_attempt
    ret = w.jpg_download("http://i.pximg.net/x/125754061_p0.jpg")
    assert isinstance(ret, list)
    assert ret[0] == "http://i.pximg.net/x/125754061_p0.jpg"
    assert len(calls) == 1, "deadline must NOT be retried 5x (wedged conn won't heal)"


def test_jpg_download_stop_propagates_not_faillist():
    w = _mk()

    def fake_attempt(url, session, timetag, meta_cache=None):
        raise DownloadStopped()

    w._jpg_attempt = fake_attempt
    with pytest.raises(DownloadStopped):
        w.jpg_download("http://i.pximg.net/x/1_p0.jpg")


# ── fetch_with_cookie_retry timeout (ugoira_meta hot path) ─────────────────

def test_fetch_with_cookie_retry_passes_timeout_first_call():
    from app.core.pixiv_thread_utils import fetch_with_cookie_retry

    seen = []

    class _R:
        status_code = 200

    def fake_get(url, headers=None, stream=None, timeout=None):
        seen.append(timeout)
        return _R()

    fetch_with_cookie_retry(fake_get, "http://x", {}, "", timeout=(10, 30))
    assert seen == [(10, 30)], "ugoira_meta fetch must be timeout-bounded (no unbounded request)"


def test_fetch_with_cookie_retry_passes_timeout_on_retry():
    from app.core.pixiv_thread_utils import fetch_with_cookie_retry

    seen = []
    seq = [403, 200]  # first 403 (no cookie) -> retry with cookie

    class _R:
        def __init__(self, code):
            self.status_code = code

    def fake_get(url, headers=None, stream=None, timeout=None):
        seen.append(timeout)
        return _R(seq.pop(0))

    fetch_with_cookie_retry(fake_get, "http://x", {}, "cookieval", timeout=(7, 21))
    assert seen == [(7, 21), (7, 21)], "both the first and the cookie-retry request must be bounded"


def test_fetch_with_cookie_retry_default_timeout_is_set():
    """Even callers that omit timeout get a bounded default (never None)."""
    from app.core.pixiv_thread_utils import fetch_with_cookie_retry

    seen = []

    class _R:
        status_code = 200

    def fake_get(url, headers=None, stream=None, timeout="MISSING"):
        seen.append(timeout)
        return _R()

    fetch_with_cookie_retry(fake_get, "http://x", {}, "")
    assert seen and seen[0] is not None and seen[0] != "MISSING"
