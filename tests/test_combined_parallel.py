import os, sys, tempfile, datetime, threading
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from queue import Queue
from app.core.thread_combined import (
    combined_thread,
    resolve_worker_count,
    COMBINED_WORKERS_CAP,
)
from app.core.account_scheduler import AccountScheduler, AccountState


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    """Point APPDATA at a tmp dir so construction never reads real %APPDATA%."""
    monkeypatch.setenv("APPDATA", str(tmp_path))


def _thread(workers=1):
    path = tempfile.mkdtemp()
    return combined_thread(
        q=Queue(), Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=path,
        single_thread_mode=True, download_path=path,
        download_time=datetime.datetime(1970, 1, 1),
        combined_workers=workers,
    )


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ── pure resolve_worker_count ──────────────────────────────────────────────
def test_resolve_worker_count_setting_one_is_sequential():
    assert resolve_worker_count(1, 8, 100) == 1
    assert resolve_worker_count(0, 8, 100) == 1
    assert resolve_worker_count("bad", 8, 100) == 1


def test_resolve_worker_count_bounded():
    assert resolve_worker_count(8, 3, 100) == 3            # account cap
    assert resolve_worker_count(8, 10, 2) == 2             # pending cap
    assert resolve_worker_count(100, 100, 100) == COMBINED_WORKERS_CAP  # hard cap
    assert resolve_worker_count(4, 10, 10) == 4            # user setting wins


# ── scheduler active count ─────────────────────────────────────────────────
def test_active_account_count_excludes_disabled():
    a, b, c = AccountState("c1", "A"), AccountState("c2", "B"), AccountState("c3", "C")
    c.disabled_reason = "proxy_dead"
    sched = AccountScheduler([a, b, c], lambda: 0, threading.Event(), threading.Event())
    assert sched.active_account_count() == 2


# ── _effective_worker_count gating ─────────────────────────────────────────
def test_effective_worker_count_one_when_setting_one():
    t = _thread(workers=1)
    assert t._effective_worker_count(50) == 1


def test_effective_worker_count_bounded_by_active_accounts():
    t = _thread(workers=8)
    a, b = AccountState("c1", "A"), AccountState("c2", "B")
    t._scheduler = AccountScheduler([a, b], lambda: 0, t._pause_event, t._stop_event)
    assert t._effective_worker_count(50) == 2


# ── wrapper preserves the sequential contract ──────────────────────────────
def test_process_one_pid_wrapper_sets_last_pid_ok(monkeypatch):
    t = _thread(workers=1)
    monkeypatch.setattr(t, "_process_one_pid_core", lambda pid, nq, **kw: (["x"], True))
    failed = t._process_one_pid("123", needs_query=True)
    assert failed == ["x"]
    assert t._last_pid_ok is True


def test_process_one_pid_wrapper_acquire_none(monkeypatch):
    t = _thread(workers=1)
    monkeypatch.setattr(t, "_process_one_pid_core", lambda pid, nq, **kw: (None, False))
    assert t._process_one_pid("123", needs_query=True) is None


# ── concurrent coordinator drains all + bookkeeps ──────────────────────────
def test_run_concurrent_processes_all_pids(monkeypatch):
    t = _thread(workers=3)
    processed, plock = [], threading.Lock()

    def stub(pid, nq, **kw):
        with plock:
            processed.append(pid)
        return [], True

    monkeypatch.setattr(t, "_process_one_pid_core", stub)
    marked = []
    monkeypatch.setattr(t.fetcher, "_mark_pid_processed", lambda pid: marked.append(pid))
    monkeypatch.setattr(t.downloader, "_emit_timechanged", lambda: None)

    order = [(str(i), True) for i in range(5)]
    failed_nested = t._run_concurrent(order, len(order), 3)

    assert sorted(processed) == [str(i) for i in range(5)]
    assert len(failed_nested) == 5
    assert sorted(marked) == [str(i) for i in range(5)]
    progress = [e for e in _drain(t._q) if e.type == "progress" and e.data == (1, 5)]
    assert len(progress) == 5


def test_run_concurrent_download_only_pid_not_marked(monkeypatch):
    """download-only PIDs (needs_query False) must NOT be retired from the
    query pending tracker."""
    t = _thread(workers=2)
    monkeypatch.setattr(t, "_process_one_pid_core", lambda pid, nq, **kw: ([], True))
    marked = []
    monkeypatch.setattr(t.fetcher, "_mark_pid_processed", lambda pid: marked.append(pid))
    monkeypatch.setattr(t.downloader, "_emit_timechanged", lambda: None)
    order = [("1", True), ("2", False), ("3", True)]
    t._run_concurrent(order, len(order), 2)
    assert sorted(marked) == ["1", "3"]


# ── distinct accounts held simultaneously (held flag) ──────────────────────
def test_concurrent_workers_get_distinct_accounts():
    t = _thread(workers=2)
    a, b = AccountState("c1", "A"), AccountState("c2", "B")
    t._pause_event.set()
    sched = AccountScheduler([a, b], lambda: 0, t._pause_event, t._stop_event)
    t._scheduler = sched
    barrier = threading.Barrier(2, timeout=5)
    held, hlock = [], threading.Lock()

    def stub(pid, nq, **kw):
        acc = sched.acquire()
        with hlock:
            held.append(acc.alias)
        barrier.wait()           # force both accounts checked out at once
        sched.release(acc, ok=True)
        return [], True

    t._process_one_pid_core = stub
    t.fetcher._mark_pid_processed = lambda pid: None
    t.downloader._emit_timechanged = lambda: None
    t._run_concurrent([("1", True), ("2", True)], 2, 2)
    assert sorted(held) == ["A", "B"]


# ── stop halts refilling ───────────────────────────────────────────────────
def test_concurrent_stop_halts_submission(monkeypatch):
    t = _thread(workers=2)
    processed, plock = [], threading.Lock()

    def stub(pid, nq, **kw):
        with plock:
            processed.append(pid)
        t._stop_event.set()
        return [], True

    monkeypatch.setattr(t, "_process_one_pid_core", stub)
    monkeypatch.setattr(t.fetcher, "_mark_pid_processed", lambda pid: None)
    monkeypatch.setattr(t.downloader, "_emit_timechanged", lambda: None)
    order = [(str(i), True) for i in range(20)]
    t._run_concurrent(order, len(order), 2)
    assert len(processed) < 20


# ── the user's hard requirement: a PID's page timetags must NOT interleave ──
def test_timetag_block_contiguous_per_pid():
    d = _thread(workers=2).downloader
    d._begin_pid_timetag_block(3)
    tags = [d._jpg_advance_timetag() for _ in range(3)]
    d._end_pid_timetag_block()
    fmt = "%Y%m%d_%H%M%S"
    secs = [datetime.datetime.strptime(s, fmt) for s in tags]
    assert (secs[1] - secs[0]).total_seconds() == 1
    assert (secs[2] - secs[1]).total_seconds() == 1


def test_timetag_blocks_disjoint_and_contiguous_across_threads():
    d = _thread(workers=2).downloader
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

    fmt = "%Y%m%d_%H%M%S"
    for name in ("X", "Y"):
        secs = [datetime.datetime.strptime(s, fmt) for s in results[name]]
        for i in range(1, len(secs)):
            assert (secs[i] - secs[i - 1]).total_seconds() == 1  # contiguous
    assert set(results["X"]).isdisjoint(set(results["Y"]))        # no interleave


def test_sequential_path_unaffected_by_block_mechanism():
    """Without a reserved block, _jpg_advance_timetag keeps advancing the global
    counter by 1 s (legacy behaviour, zero regression)."""
    d = _thread(workers=1).downloader
    fmt = "%Y%m%d_%H%M%S"
    a = datetime.datetime.strptime(d._jpg_advance_timetag(), fmt)
    b = datetime.datetime.strptime(d._jpg_advance_timetag(), fmt)
    assert (b - a).total_seconds() == 1
