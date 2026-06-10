"""Tests for the download-folder mtime-signature scan cache.

The expensive part of ``sync_exist_pid_with_download_folder`` is the full
``os.walk`` over (in the real app) 200k+ files. The cache now records each
visited directory's ``st_mtime_ns`` so an unchanged folder skips the walk
entirely (O(#dirs) stat calls), and only the *delta* of newly-discovered
PIDs is shadow-written to the DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.core.pixiv_thread_utils as ptu
from app.core.pixiv_thread_utils import sync_exist_pid_with_download_folder


def _mk(tmp_path):
    base = tmp_path / "base"
    dl = tmp_path / "download"
    base.mkdir()
    dl.mkdir()
    return base, dl


def test_first_run_scans_then_second_run_hits_cache(tmp_path, monkeypatch):
    base, dl = _mk(tmp_path)
    (dl / "111110_p0.jpg").write_bytes(b"")

    r1 = sync_exist_pid_with_download_folder(str(base), str(dl))
    assert r1["used_cache"] is False
    assert "111110" in r1["merged_set"]

    # Second run: nothing changed -> must NOT walk the tree.
    def _boom(*a, **k):
        raise AssertionError("scan should be skipped on a cache hit")

    monkeypatch.setattr(ptu, "_scan_download_folder", _boom)
    r2 = sync_exist_pid_with_download_folder(str(base), str(dl))
    assert r2["used_cache"] is True
    assert "111110" in r2["merged_set"]


def test_new_file_invalidates_cache(tmp_path):
    base, dl = _mk(tmp_path)
    (dl / "111110_p0.jpg").write_bytes(b"")
    sync_exist_pid_with_download_folder(str(base), str(dl))

    # Adding a file updates the directory mtime -> cache miss -> rescan.
    (dl / "222220_p0.jpg").write_bytes(b"")
    r = sync_exist_pid_with_download_folder(str(base), str(dl))
    assert r["used_cache"] is False
    assert {"111110", "222220"} <= r["merged_set"]


def test_new_subdir_file_invalidates_via_parent_mtime(tmp_path):
    base, dl = _mk(tmp_path)
    (dl / "111110_p0.jpg").write_bytes(b"")
    sync_exist_pid_with_download_folder(str(base), str(dl))

    sub = dl / "artistX"
    sub.mkdir()  # creating a sub-dir bumps dl's mtime
    (sub / "333330_p0.jpg").write_bytes(b"")

    r = sync_exist_pid_with_download_folder(str(base), str(dl))
    assert r["used_cache"] is False
    assert "333330" in r["merged_set"]


def test_only_delta_is_shadow_written(tmp_path, monkeypatch):
    base, dl = _mk(tmp_path)
    (dl / "111110_p0.jpg").write_bytes(b"")
    sync_exist_pid_with_download_folder(str(base), str(dl))

    seen = {"pids": None}

    def _capture(base_path, scanned_pids, *, event_log=None):
        seen["pids"] = set(scanned_pids)

    monkeypatch.setattr(ptu, "_shadow_write_exist_pid_to_db", _capture)

    # Add one new file: only the new PID should be shadow-written.
    (dl / "222220_p0.jpg").write_bytes(b"")
    sync_exist_pid_with_download_folder(str(base), str(dl))
    assert seen["pids"] == {"222220"}


def test_cache_hit_does_not_shadow_write(tmp_path, monkeypatch):
    base, dl = _mk(tmp_path)
    (dl / "111110_p0.jpg").write_bytes(b"")
    sync_exist_pid_with_download_folder(str(base), str(dl))

    called = {"n": 0}

    def _capture(*a, **k):
        called["n"] += 1

    monkeypatch.setattr(ptu, "_shadow_write_exist_pid_to_db", _capture)
    r = sync_exist_pid_with_download_folder(str(base), str(dl))
    assert r["used_cache"] is True
    assert called["n"] == 0, "cache hit must not re-import anything"
