"""Per-PID shared download timetag: all pages of one artwork get ONE timestamp,
consecutive PIDs differ by >=1s, concurrent PIDs stay disjoint.

This replaces the older "contiguous +1s per page" behaviour. Uniqueness of the
filename is carried by PID + page suffix, not the timetag, so a shared stamp
per PID is collision-safe and groups an artwork's pages as a unit.
"""
import datetime
import os
import sys
import threading
import tempfile
from pathlib import Path
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_combined import combined_thread

_FMT = "%Y%m%d_%H%M%S"


def _downloader(tmp, **kw):
    """A fully-constructed download_thread (via the combined orchestrator, which
    is the proven construction path that supplies all the deferred-scan defaults)."""
    return combined_thread(
        q=Queue(), Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=tmp,
        single_thread_mode=True, download_path=tmp,
        download_time=datetime.datetime(1970, 1, 1),
        **kw,
    ).downloader


def test_block_hands_same_stamp_to_every_page(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    d._begin_pid_timetag_block(3)
    tags = [d._jpg_advance_timetag() for _ in range(3)]
    d._end_pid_timetag_block()
    assert tags[0] == tags[1] == tags[2]


def test_consecutive_pid_blocks_advance_by_one_second(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    stamps = []
    for _ in range(3):
        d._begin_pid_timetag_block(2)
        stamps.append(d._jpg_advance_timetag())  # one page is enough; all share it
        d._end_pid_timetag_block()
    secs = [datetime.datetime.strptime(s, _FMT) for s in stamps]
    assert (secs[1] - secs[0]).total_seconds() == 1
    assert (secs[2] - secs[1]).total_seconds() == 1


def test_concurrent_pids_share_within_disjoint_across(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    results = {}
    start = threading.Barrier(2, timeout=5)

    def worker(name, n):
        start.wait()
        d._begin_pid_timetag_block(n)
        results[name] = [d._jpg_advance_timetag() for _ in range(n)]
        d._end_pid_timetag_block()

    t1 = threading.Thread(target=worker, args=("X", 4))
    t2 = threading.Thread(target=worker, args=("Y", 4))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(set(results["X"])) == 1            # X's pages share one stamp
    assert len(set(results["Y"])) == 1            # Y's pages share one stamp
    assert set(results["X"]).isdisjoint(set(results["Y"]))  # different PIDs differ


def test_download_pid_group_shares_one_stamp_across_pages(tmp_path, monkeypatch):
    """Step 4's per-PID group download owns the block, so every page of the PID
    gets the same stamp without the caller having to manage it."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    seen = []
    monkeypatch.setattr(d, "_apply_live_settings_if_changed", lambda: None)
    monkeypatch.setattr(d, "_sleep_within_pid", lambda *a, **k: None)

    def fake_gif_or_jpg(url, session=None):
        seen.append(d._jpg_advance_timetag())
        return 0

    monkeypatch.setattr(d, "gif_or_jpg", fake_gif_or_jpg)
    d._download_pid_group("130008458",
                          ["http://x/a_p0.jpg", "http://x/a_p1.jpg", "http://x/a_p2.jpg"])
    assert len(seen) == 3
    assert len(set(seen)) == 1  # all three pages share exactly one stamp
