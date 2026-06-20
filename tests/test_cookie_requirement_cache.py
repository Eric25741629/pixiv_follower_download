"""Tests for the in-process cache of ``pixiv_cookie_requirement.json``.

``get_pixiv_cookie_requirement`` is on the combined-mode download hot path
(``thread_download._resolve_pid_and_cookie`` calls it per page when the cached
meta has no ``requires_cookie``, and it can repeat inside the JPEG retry loop).
Previously each call re-read + re-parsed the ENTIRE file. These tests pin the
process-global cache keyed by a cheap (size, mtime_ns) file signature, mirroring
the ``closed_artwork_set`` cache pattern. The cache must:
  * read+parse the file ONCE across many calls while it is unchanged,
  * return correct values served from cache,
  * recompute automatically after the file changes (signature mismatch),
  * include a micro-benchmark asserting the read-count win.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import pixiv_api


def _trace_path(tmp_path):
    base = os.path.join(str(tmp_path), "pixiv_download")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "pixiv_cookie_requirement.json")


def _write_synthetic(path, n=400, *, true_pids=("100", "250")):
    """Write a JSON map of ``n`` distinct pids; a couple flagged requires_cookie."""
    data = {}
    for i in range(n):
        pid = str(i)
        data[pid] = {"requires_cookie": pid in set(true_pids)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def _install_read_counter(monkeypatch):
    """Wrap ``pixiv_api.safe_read_json`` so we count actual file reads."""
    counter = {"reads": 0}
    real = pixiv_api.safe_read_json

    def counting(file_path, default=None):
        counter["reads"] += 1
        return real(file_path, default)

    monkeypatch.setattr(pixiv_api, "safe_read_json", counting)
    return counter


def test_file_read_once_across_many_distinct_pids(monkeypatch, tmp_path):
    """Many calls for distinct pids must parse the file exactly ONCE."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = _trace_path(tmp_path)
    _write_synthetic(path, n=400)
    counter = _install_read_counter(monkeypatch)

    # 800 lookups across all 400 distinct pids (each twice).
    for pid in list(range(400)) + list(range(400)):
        pixiv_api.get_pixiv_cookie_requirement(str(pid))

    assert counter["reads"] == 1, (
        f"expected a single file read for 800 calls, got {counter['reads']}"
    )


def test_returned_values_correct_from_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = _trace_path(tmp_path)
    _write_synthetic(path, n=300, true_pids=("100", "250"))
    _install_read_counter(monkeypatch)

    assert pixiv_api.get_pixiv_cookie_requirement("100") is True
    assert pixiv_api.get_pixiv_cookie_requirement("250") is True
    assert pixiv_api.get_pixiv_cookie_requirement("7") is False
    # unknown pid -> None
    assert pixiv_api.get_pixiv_cookie_requirement("999999") is None


def test_cache_invalidates_after_file_change(monkeypatch, tmp_path):
    """After the file's signature changes the next call re-reads and reflects it."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = _trace_path(tmp_path)
    _write_synthetic(path, n=50, true_pids=())
    counter = _install_read_counter(monkeypatch)

    # First reads -> one parse; pid "10" not requiring cookie yet.
    assert pixiv_api.get_pixiv_cookie_requirement("10") is False
    assert pixiv_api.get_pixiv_cookie_requirement("10") is False
    assert counter["reads"] == 1

    # Rewrite the file with a different size + a guaranteed-newer mtime so the
    # signature changes even on coarse-resolution filesystems.
    _write_synthetic(path, n=80, true_pids=("10",))
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    # Next call must re-read (signature mismatch) and see the new value.
    assert pixiv_api.get_pixiv_cookie_requirement("10") is True
    assert counter["reads"] == 2
    # And cached again afterward.
    assert pixiv_api.get_pixiv_cookie_requirement("10") is True
    assert counter["reads"] == 2


def test_missing_file_returns_none_and_is_cheap(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # No file written; ensure directory exists so only the JSON file is missing.
    os.makedirs(os.path.join(str(tmp_path), "pixiv_download"), exist_ok=True)
    assert pixiv_api.get_pixiv_cookie_requirement("123") is None


def test_microbenchmark_cache_beats_uncached_reads(monkeypatch, tmp_path):
    """Synthetic micro-benchmark: cached path issues 1 read vs N uncached reads.

    Proves the win is a real reduction in file parses, not just wall-clock noise.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = _trace_path(tmp_path)
    _write_synthetic(path, n=400)

    n_calls = 500

    # Cached (current behaviour): count real file reads + time it.
    counter = _install_read_counter(monkeypatch)
    t0 = time.perf_counter()
    for i in range(n_calls):
        pixiv_api.get_pixiv_cookie_requirement(str(i % 400))
    cached_secs = time.perf_counter() - t0
    cached_reads = counter["reads"]

    # Uncached baseline: force a fresh parse on every call by stubbing the
    # cache loader to bypass memoisation (re-read each time).
    raw_reads = {"n": 0}

    def uncached_loader(p):
        raw_reads["n"] += 1
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}

    monkeypatch.setattr(pixiv_api, "_load_cookie_requirement_map", uncached_loader)
    t0 = time.perf_counter()
    for i in range(n_calls):
        pixiv_api.get_pixiv_cookie_requirement(str(i % 400))
    uncached_secs = time.perf_counter() - t0

    print(
        f"\n[cookie-requirement-cache micro-benchmark] {n_calls} calls\n"
        f"  cached:   reads={cached_reads:<4d} time={cached_secs*1000:.2f} ms\n"
        f"  uncached: reads={raw_reads['n']:<4d} time={uncached_secs*1000:.2f} ms\n"
        f"  read-count reduction: {raw_reads['n']}x -> {cached_reads}x"
    )

    assert cached_reads == 1
    assert raw_reads["n"] == n_calls
    # The whole point: the cached path does far fewer file reads.
    assert cached_reads < raw_reads["n"]
