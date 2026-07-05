"""Cross-run persistence of the per-PID timetag assignment (pid_timetags.json).

Root cause of the 122820069 p0/p1 split: the pid->stamp pre-allocation lived
only in one run's memory, so a page retried by a later run got a fresh stamp
while its siblings kept the old prefix. The sidecar records the assignment;
the next run reuses a queued PID's recorded stamp so retried pages join their
on-disk siblings. Completed PIDs (no longer queued) fall out on save.
"""
import datetime
import json
import os
import sys
from pathlib import Path
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_combined import combined_thread

_FMT = "%Y%m%d_%H%M%S"
_EPOCH = datetime.datetime(1970, 1, 1)


def _downloader(tmp, download_time=_EPOCH):
    return combined_thread(
        q=Queue(), Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=tmp,
        single_thread_mode=True, download_path=tmp,
        download_time=download_time,
    ).downloader


def _sidecar(tmp):
    return os.path.join(str(tmp), "pid_timetags.json")


def test_assign_writes_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = _downloader(str(tmp_path))
    d.assign_pid_timetags(["a", "b"])
    data = json.loads(Path(_sidecar(tmp_path)).read_text(encoding="utf-8"))
    assert data == {"a": "19700101_000000", "b": "19700101_000001"}


def test_second_run_reuses_recorded_stamp_for_retried_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d1 = _downloader(str(tmp_path))
    d1.assign_pid_timetags(["a", "b"])  # run A: a=0s, b=1s; cursor -> 2s

    # Run B: "a" completed, "b" retried, "c" new. Cursor resumes where A left it.
    d2 = _downloader(str(tmp_path), download_time=d1.download_time)
    d2.assign_pid_timetags(["b", "c"])
    assert d2._pid_timetag["b"] == _EPOCH + datetime.timedelta(seconds=1)  # reused
    assert d2._pid_timetag["c"] == _EPOCH + datetime.timedelta(seconds=2)  # fresh
    # Cursor advanced only by the fresh count.
    assert d2.download_time == _EPOCH + datetime.timedelta(seconds=3)
    # Completed "a" pruned from the sidecar.
    data = json.loads(Path(_sidecar(tmp_path)).read_text(encoding="utf-8"))
    assert set(data) == {"b", "c"}


def test_all_reused_does_not_emit_timechanged(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d1 = _downloader(str(tmp_path))
    d1.assign_pid_timetags(["a"])
    d2 = _downloader(str(tmp_path), download_time=d1.download_time)
    while not d2._q.empty():
        d2._q.get_nowait()
    d2.assign_pid_timetags(["a"])
    events = []
    while not d2._q.empty():
        events.append(d2._q.get_nowait())
    assert not [e for e in events if getattr(e, "type", None) == "timechanged"]
    assert d2.download_time == d1.download_time  # cursor untouched


def test_corrupt_sidecar_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    Path(_sidecar(tmp_path)).write_text("{not json", encoding="utf-8")
    d = _downloader(str(tmp_path))
    d.assign_pid_timetags(["a"])
    assert d._pid_timetag["a"] == _EPOCH  # fresh assignment, no crash
