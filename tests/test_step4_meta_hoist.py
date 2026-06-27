"""FIX #4 — per-page meta resolution is hoisted out of the Step 4 retry loop.

``jpg_download`` retries ``_jpg_attempt`` up to 5×. Before the fix every attempt
re-ran the read-only meta resolution: ``_resolve_pid_and_cookie`` (a
``_get_meta``/SQLite SELECT for ``requires_cookie``) PLUS ``_load_artwork_metadata``
(another ``_get_meta``/SELECT) — ~2× ``get_meta`` per attempt, so a page that
retries N times paid 2×N SELECTs. After the fix the resolution is memoised in a
1-slot cache for the whole ``jpg_download`` call, so the DB ``get_meta`` count no
longer scales with the attempt count.

Behavior preserved: the per-attempt SIDE EFFECTS stay per-attempt — the
``_record_cookie_usage`` counter bump and the ``self._pid_cookie_used`` stamp.
"""
from pathlib import Path
import datetime
import sys
import threading

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread

# A minimal VALID JPEG (header + padding + footer, >= MIN_IMAGE_BYTES=64) so the
# download-integrity validator (validate_image_file, merged from
# feat/download-integrity) accepts the body. These tests assert meta-resolution
# COUNT, not body content — they only need a body that isn't rejected as a
# truncated/invalid image.
_VALID_JPEG = b"\xff\xd8\xff" + b"\x00" * 70 + b"\xff\xd9"


class _CountingDB:
    """Stand-in for ``self._metadata_db`` that counts ``get_meta`` SELECTs."""

    def __init__(self, meta):
        self._meta = meta
        self.get_meta_calls = 0

    def get_meta(self, pid_key):
        self.get_meta_calls += 1
        return dict(self._meta)

    def mark_artwork_revoked(self, pid):  # pragma: no cover - not hit here
        pass


class _FakeResp:
    """A streamable 200 response yielding a fixed body."""

    def __init__(self, body=_VALID_JPEG):
        self._body = body
        self.status_code = 200
        self.closed = False

    def iter_content(self, chunk_size=None):
        yield self._body

    def raise_for_status(self):
        pass

    def close(self):
        self.closed = True


class _FakeSession:
    """``get`` raises a retryable error ``fail_times`` times, then returns 200."""

    def __init__(self, fail_times=0, body=_VALID_JPEG):
        self.fail_times = fail_times
        self._body = body
        self.get_calls = 0

    def get(self, url, headers=None, stream=None, timeout=None):
        self.get_calls += 1
        if self.get_calls <= self.fail_times:
            # A generic (retryable, non-proxy) error -> jpg_download retries.
            raise requests.exceptions.HTTPError("transient")
        return _FakeResp(self._body)


def _make_worker(db, tmp_path):
    """Construct a download_thread skeleton via __new__ wired for jpg_download.

    Uses the REAL _get_meta (base mixin) so DB SELECTs are genuinely counted;
    url_meta is empty so every _get_meta falls through to db.get_meta. The two
    side-effecting bits of _resolve_pid_and_cookie (cookie selection + usage
    counter) are stubbed so we can assert their per-attempt timing directly.
    """
    w = download_thread.__new__(download_thread)
    # _get_meta plumbing: empty in-memory cache forces the DB path.
    w.url_meta = {}
    w._url_meta_lock = threading.Lock()
    w._metadata_db = db
    # Deterministic, side-effect-free cookie selection.
    w._select_cookie_for_pid = lambda pid: "COOKIE"
    # Count how many times the per-attempt usage bump fires.
    w.record_usage_calls = []
    w._record_cookie_usage = lambda stage, pid, cookie: w.record_usage_calls.append((stage, pid))
    w._pid_cookie_used = {}
    # jpg machinery
    w.agent = "UA"
    w.notag = True
    w.notime = False
    w.filename_template = ""
    w.set_file_mtime = False
    w._stats_collector = None
    w._download_deadline_sec = 5.0
    w.timelock = threading.Lock()
    w.download_time = datetime.datetime(2026, 1, 1, 0, 0, 0)
    w._build_hashtag_text = lambda tag, max_len=0: " "
    w._resolve_download_target_dir = lambda *a, **k: str(tmp_path)
    w._enqueue_jxl = lambda path: None
    # Skip the real stop-aware backoff between retries (no _stop_event on a
    # __new__ skeleton); return True = "full duration elapsed, keep retrying".
    w._wait_interruptible = lambda seconds: True
    return w


_URL = "https://i.pximg.net/img-original/img/x/777_p0.jpg"
# DB meta carries BOTH requires_cookie (for _resolve_pid_and_cookie) and tag (so
# _load_artwork_metadata returns from the dict — no HTTP Pixiv_info fallback).
_DB_META = {"requires_cookie": False, "tag": ["safe"], "like": 10, "pagecount": 1, "img_url": None}


def test_meta_resolution_does_not_scale_with_retries(tmp_path):
    """fail twice then succeed (3 attempts) -> get_meta still resolved ONCE."""
    db = _CountingDB(_DB_META)
    w = _make_worker(db, tmp_path)
    session = _FakeSession(fail_times=2)

    result = w.jpg_download(_URL, session=session)

    assert result == 0, "third attempt should succeed and return 0"
    assert session.get_calls == 3, "two transient failures then one success"
    # The expensive read-only resolution (2 SELECTs: requires_cookie + artwork
    # meta) runs ONCE for the page despite 3 attempts — NOT 2x per attempt (=6).
    assert db.get_meta_calls == 2, (
        f"meta resolved once per page; got {db.get_meta_calls} SELECTs "
        f"(would be 6 = 2x3 attempts without the hoist)"
    )
    # Behavior preserved: the per-attempt cookie-usage bump still fires EACH
    # attempt (3x), and the _pid_cookie_used stamp landed.
    assert len(w.record_usage_calls) == 3, "usage counter must stay per-attempt"
    assert w._pid_cookie_used.get("777") is False


def test_clean_single_attempt_resolves_meta_once_and_downloads(tmp_path):
    """A clean first-attempt download resolves meta once and writes the file."""
    db = _CountingDB(_DB_META)
    w = _make_worker(db, tmp_path)
    session = _FakeSession(fail_times=0, body=_VALID_JPEG)

    result = w.jpg_download(_URL, session=session)

    assert result == 0
    assert session.get_calls == 1
    # Single attempt: the resolution's two SELECTs, once.
    assert db.get_meta_calls == 2
    assert len(w.record_usage_calls) == 1
    # The file landed under the resolved target dir with the page/ext preserved.
    written = list(tmp_path.glob("*.jpg"))
    assert len(written) == 1, "exactly one jpg written"
    assert written[0].read_bytes() == _VALID_JPEG


def test_micro_benchmark_get_meta_calls_constant_vs_attempts(tmp_path):
    """Synthetic micro-benchmark asserting the win: DB SELECT count is constant
    across attempt counts (the whole point of the hoist).

    BEFORE (per-attempt resolve): get_meta == 2 * attempts.
    AFTER  (hoisted/memoised):    get_meta == 2 (constant).
    """
    samples = {}
    for fail_times in (0, 2, 4):
        db = _CountingDB(_DB_META)
        w = _make_worker(db, tmp_path)
        # fail_times=4 -> 5th attempt succeeds (the loop's max of 5).
        session = _FakeSession(fail_times=fail_times)
        w.jpg_download(_URL, session=session)
        samples[fail_times + 1] = db.get_meta_calls  # attempts -> SELECT count

    # AFTER: flat 2 regardless of attempt count.
    assert set(samples.values()) == {2}, f"SELECTs must be constant; got {samples}"
    # The would-be BEFORE cost (2 * attempts) is strictly larger for >1 attempt.
    before = {att: 2 * att for att in samples}
    assert samples[5] == 2 and before[5] == 10, (
        f"5-attempt page: AFTER={samples[5]} SELECTs vs BEFORE={before[5]} SELECTs"
    )
