"""Tests for _execute_downloads_pool's per-author batching/barrier and the
off-path single-batch regression. Uses __new__ + stubs."""
from pathlib import Path
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


class _FakeQ:
    def put(self, ev):
        pass


def _make_pool_thread(record):
    t = download_thread.__new__(download_thread)
    t._q = _FakeQ()
    t._stop_event = threading.Event()
    t._step4_pid_total = 0
    t._step4_pid_done = 0
    t._emit_phase = lambda *a, **k: None
    t._maybe_flush_url_meta_periodically = lambda done: None
    lock = threading.Lock()

    def _dl(pid, urls):
        with lock:
            record.append(pid)
        return []

    t._download_pid_group = _dl
    return t


def test_pool_processes_each_author_batch_before_the_next():
    record = []
    t = _make_pool_thread(record)
    batch_a = ["30", "10"]
    batch_b = ["99"]
    groups = {"30": ["u"], "10": ["u"], "99": ["u"]}
    t._step4_pid_total = 3
    t._execute_downloads_pool([batch_a, batch_b], groups)
    # All of batch A must be recorded before any of batch B — the next
    # batch isn't submitted until the current batch's futures all drain.
    assert set(record[:2]) == {"30", "10"}
    assert record[2] == "99"


def test_pool_single_batch_processes_all_pids_offpath():
    record = []
    t = _make_pool_thread(record)
    pid_order = ["30", "10", "20"]
    groups = {p: ["u"] for p in pid_order}
    t._step4_pid_total = 3
    failed = t._execute_downloads_pool([pid_order], groups)
    assert set(record) == {"30", "10", "20"}
    assert len(failed) == 3


def test_pool_stops_between_batches_when_stop_set():
    record = []
    t = _make_pool_thread(record)
    t._stop_event.set()
    t._execute_downloads_pool([["1"], ["2"]], {"1": ["u"], "2": ["u"]})
    assert record == []   # stop checked before submitting the first batch
