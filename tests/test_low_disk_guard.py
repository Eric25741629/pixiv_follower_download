"""Low-disk boundary guard: when the download drive has less than
LOW_DISK_MIN_FREE_BYTES free, the worker must stop and notify the user
instead of grinding out 0-byte / failed writes (the 2026-07-04 disk-full
incident: F: hit 0 bytes free and a 4-day run mass-failed 35k pages).
"""
import queue
import sys
import types

import pytest

sys.path.insert(0, ".")

from app.core.thread_download import LOW_DISK_MIN_FREE_BYTES, download_thread


class _Stub:
    """Minimal object carrying only what the guard reads."""

    def __init__(self, tmp_path):
        self.path = str(tmp_path)
        self._q = queue.Queue()
        self._low_disk_notified = False
        self._stopped = False

    def stop(self):
        self._stopped = True

    _check_disk_space_or_stop = download_thread._check_disk_space_or_stop


def _events(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_threshold_is_100mb():
    assert LOW_DISK_MIN_FREE_BYTES == 100 * 1024 * 1024


def test_plenty_of_space_passes(tmp_path):
    w = _Stub(tmp_path)
    assert w._check_disk_space_or_stop() is True
    assert not w._stopped
    assert _events(w._q) == []


def test_low_space_stops_and_notifies(tmp_path, monkeypatch):
    w = _Stub(tmp_path)
    monkeypatch.setattr(
        "app.core.thread_download.shutil.disk_usage",
        lambda p: types.SimpleNamespace(total=1, used=1, free=50 * 1024 * 1024),
    )
    assert w._check_disk_space_or_stop() is False
    assert w._stopped
    evs = _events(w._q)
    assert any(e.type == "output" and "磁碟" in str(e.data) for e in evs)


def test_notifies_only_once(tmp_path, monkeypatch):
    w = _Stub(tmp_path)
    monkeypatch.setattr(
        "app.core.thread_download.shutil.disk_usage",
        lambda p: types.SimpleNamespace(total=1, used=1, free=0),
    )
    assert w._check_disk_space_or_stop() is False
    _events(w._q)
    assert w._check_disk_space_or_stop() is False
    assert _events(w._q) == []


def test_unreadable_path_never_blocks(tmp_path, monkeypatch):
    w = _Stub(tmp_path)
    monkeypatch.setattr(
        "app.core.thread_download.shutil.disk_usage",
        lambda p: (_ for _ in ()).throw(OSError("gone")),
    )
    assert w._check_disk_space_or_stop() is True
    assert not w._stopped
