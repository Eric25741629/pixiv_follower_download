"""Tests for _execute_downloads_pool's per-author batching/barrier and the
off-path single-batch regression. Uses __new__ + stubs."""
import datetime
from pathlib import Path
import sys
import threading
import queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.account_scheduler import AccountScheduler, AccountState
from app.core.thread_download import download_thread


class _FakeQ:
    def put(self, ev):
        pass


def _make_pool_thread(record):
    t = download_thread.__new__(download_thread)
    t._q = _FakeQ()
    t._stop_event = threading.Event()
    t._pause_event = threading.Event()
    t._pause_event.set()
    t._scheduler = None
    t._pid_cookie_selection = {}
    t._current_account_local = threading.local()
    t.timelock = threading.Lock()
    t.download_time = datetime.datetime(2026, 1, 1)
    t._step4_pid_total = 0
    t._step4_pid_done = 0
    t._emit_phase = lambda *a, **k: None
    t._maybe_flush_url_meta_periodically = lambda done: None
    t._run_with_network_retry = lambda _label, fn: (True, fn(), None)
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


def test_pool_scheduler_success_refreshes_cookie_first_success():
    record = []
    t = _make_pool_thread(record)
    refreshed = []
    scheduler = AccountScheduler(
        accounts=[AccountState(cookie="c1", alias="A1")],
        get_cooldown_avg=lambda: 0,
        pause_event=t._pause_event,
        stop_event=t._stop_event,
        on_success=lambda acc: refreshed.append(acc.cookie),
    )
    t._scheduler = scheduler

    failed = t._execute_downloads_pool([["10"]], {"10": ["u"]})

    assert failed == [[]]
    assert refreshed == ["c1"]
    assert t._pid_cookie_selection == {"10": "c1"}


def test_pool_scheduler_network_failure_disables_cookie():
    record = []
    t = _make_pool_thread(record)
    disabled = []
    scheduler = AccountScheduler(
        accounts=[AccountState(cookie="c1", alias="A1")],
        get_cooldown_avg=lambda: 0,
        pause_event=t._pause_event,
        stop_event=t._stop_event,
        on_disable=lambda acc: disabled.append(acc.cookie),
    )
    t._scheduler = scheduler
    t._run_with_network_retry = lambda _label, _fn: (False, None, RuntimeError("proxy down"))

    failed = t._execute_downloads_pool([["10"]], {"10": ["u"]})

    assert failed == [[]]
    assert disabled == ["c1"]


class _ImmediateScheduler:
    def __init__(self, accounts):
        self._accounts = queue.Queue()
        for acc in accounts:
            self._accounts.put(acc)
        self.released = []

    def acquire(self):
        try:
            return self._accounts.get_nowait()
        except queue.Empty:
            return None

    def release(self, account, ok=True):
        self.released.append((account.cookie, ok))


def test_pool_scheduler_current_account_is_thread_local_per_worker():
    t = _make_pool_thread([])
    t._scheduler = _ImmediateScheduler([
        AccountState(cookie="c1", alias="A1"),
        AccountState(cookie="c2", alias="A2"),
    ])
    t._run_with_network_retry = lambda _label, fn: (True, fn(), None)
    barrier = threading.Barrier(2)
    seen = {}
    seen_lock = threading.Lock()

    def _dl(pid, _urls):
        barrier.wait(timeout=2)
        acc = t._current_download_account()
        with seen_lock:
            seen[pid] = acc.cookie if acc is not None else None
        return []

    t._download_pid_group = _dl

    t._execute_downloads_pool([["10", "20"]], {"10": ["u"], "20": ["u"]})

    assert seen == {"10": "c1", "20": "c2"}
    assert sorted(t._scheduler.released) == [("c1", True), ("c2", True)]
