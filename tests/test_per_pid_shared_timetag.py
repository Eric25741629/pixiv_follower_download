"""Per-PID PRE-ALLOCATED download timetag: stamps are assigned by queue position
before downloads start (assign_pid_timetags), then each PID's pages share its one
stamp via an O(1) map lookup — no lock, no global-counter race during concurrent
downloads.

Uniqueness of the filename is carried by PID + page suffix, not the timetag, so a
shared stamp per PID is collision-safe and groups an artwork's pages as a unit.
"""
import datetime
import sys
import threading
from pathlib import Path
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_combined import combined_thread

_FMT = "%Y%m%d_%H%M%S"
_EPOCH = datetime.datetime(1970, 1, 1)


def _downloader(tmp, **kw):
    """A fully-constructed download_thread (via the combined orchestrator, which
    is the proven construction path that supplies all the deferred-scan defaults)."""
    return combined_thread(
        q=Queue(), Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=tmp,
        single_thread_mode=True, download_path=tmp,
        download_time=_EPOCH,
        **kw,
    ).downloader


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except Exception:
            break
    return out


def test_assign_builds_map_advances_and_persists_once(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    _drain(d._q)  # discard construction-time events
    d.assign_pid_timetags(["a", "b", "c"])
    assert d._pid_timetag["a"] == _EPOCH
    assert (d._pid_timetag["c"] - d._pid_timetag["a"]).total_seconds() == 2
    # Global cursor advanced past the whole block and persisted EXACTLY once.
    assert d.download_time == _EPOCH + datetime.timedelta(seconds=3)
    tc = [m for m in _drain(d._q) if getattr(m, "type", None) == "timechanged"]
    assert len(tc) == 1
    assert tc[0].data == "1970-01-01 00:00:03"


def test_block_hands_same_stamp_to_every_page(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    d.assign_pid_timetags(["p1"])
    d._begin_pid_timetag_block("p1")
    tags = [d._jpg_advance_timetag() for _ in range(3)]
    d._end_pid_timetag_block()
    assert tags[0] == tags[1] == tags[2]


def test_consecutive_pid_blocks_advance_by_one_second(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    d.assign_pid_timetags(["p1", "p2", "p3"])
    stamps = []
    for p in ("p1", "p2", "p3"):
        d._begin_pid_timetag_block(p)
        stamps.append(d._jpg_advance_timetag())  # one page is enough; all share it
        d._end_pid_timetag_block()
    secs = [datetime.datetime.strptime(s, _FMT) for s in stamps]
    assert (secs[1] - secs[0]).total_seconds() == 1
    assert (secs[2] - secs[1]).total_seconds() == 1


def test_concurrent_pids_share_within_disjoint_across(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    d.assign_pid_timetags(["X", "Y"])
    results = {}
    start = threading.Barrier(2, timeout=5)

    def worker(name, n):
        start.wait()
        d._begin_pid_timetag_block(name)
        results[name] = [d._jpg_advance_timetag() for _ in range(n)]
        d._end_pid_timetag_block()

    t1 = threading.Thread(target=worker, args=("X", 4))
    t2 = threading.Thread(target=worker, args=("Y", 4))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(set(results["X"])) == 1            # X's pages share one stamp
    assert len(set(results["Y"])) == 1            # Y's pages share one stamp
    assert set(results["X"]).isdisjoint(set(results["Y"]))  # different PIDs differ


def test_unassigned_pid_falls_back_to_lazy_reserve(tmp_path, monkeypatch):
    """A PID never passed to assign_pid_timetags (e.g. a requeued straggler) still
    gets one shared stamp via the legacy lazy +1s reserve — no crash, no None."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    d.assign_pid_timetags(["known"])
    d._begin_pid_timetag_block("not-in-map")
    tags = [d._jpg_advance_timetag() for _ in range(2)]
    d._end_pid_timetag_block()
    assert tags[0] == tags[1]  # still one shared stamp for the PID


def test_apply_live_settings_does_not_rewind_pre_allocated_cursor(tmp_path, monkeypatch):
    """Regression: assign emits the advanced cursor up front, BEFORE the GUI
    persists it, so the settings file still holds the OLD start. A mid-run
    live-settings apply must NOT treat that stale value as a user edit and rewind
    download_time (which would stick the cursor and stamp duplicate prefixes
    across runs — exactly the bug the whole machinery exists to prevent)."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    d.assign_pid_timetags(["p1", "p2"])
    advanced = d.download_time  # _EPOCH + 2s
    assert advanced == _EPOCH + datetime.timedelta(seconds=2)

    class _StaleLive:
        # settings file still reports the pre-assign start
        def signature(self):
            return "changed-once"

        def sections(self):
            return {"download": {"download_time": "1970-01-01 00:00:00"}}

    d._live = _StaleLive()
    d._live_sig = None
    d._apply_live_settings_if_changed()
    assert d.download_time == advanced  # NOT rewound to 1970-01-01 00:00:00


def test_download_pid_group_shares_one_stamp_across_pages(tmp_path, monkeypatch):
    """Step 4's per-PID group download owns the block (looks the stamp up by pid),
    so every page of the PID gets the same stamp without the caller managing it."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    d.assign_pid_timetags(["130008458"])
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
    assert seen[0] == _EPOCH.strftime(_FMT)  # and it's the pre-allocated stamp
