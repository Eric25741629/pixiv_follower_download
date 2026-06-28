"""Regression tests for the 2026-06-13 fixes:

- AccountScheduler held flag (multi-consumer pool workers must never check
  out the same account concurrently) + release_neutral + once-only
  all-disabled message + countdown throttle.
- download_thread: timechanged persistence emits, stop/non-network-exception
  release semantics, file-mtime stamping, live download_time guard.
"""
import datetime
import os
import queue
import threading
import time

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.account_scheduler import AccountScheduler, AccountState
from app.core.thread_download import download_thread


def _scheduler(accounts, q=None, **kw):
    pause = threading.Event()
    pause.set()
    stop = threading.Event()
    return AccountScheduler(
        accounts=accounts,
        get_cooldown_avg=lambda: 0,
        pause_event=pause,
        stop_event=stop,
        q=q,
        **kw,
    ), stop


def test_acquire_marks_held_and_skips_held_accounts():
    a1 = AccountState(cookie="c1", alias="A1")
    a2 = AccountState(cookie="c2", alias="A2")
    sched, _stop = _scheduler([a1, a2])

    first = sched.acquire()
    assert first.held is True
    second = sched.acquire()
    assert second is not first
    assert {first.cookie, second.cookie} == {"c1", "c2"}


def test_release_clears_held_and_allows_reacquire():
    a1 = AccountState(cookie="c1", alias="A1")
    sched, _stop = _scheduler([a1])
    acc = sched.acquire()
    assert acc.held
    sched.release(acc, ok=True)
    assert acc.held is False


def test_release_neutral_no_callbacks_but_cooldown_applied():
    fired = []
    a1 = AccountState(cookie="c1", alias="A1")
    sched, _stop = _scheduler(
        [a1],
        on_disable=lambda acc: fired.append(("disable", acc.cookie)),
        on_success=lambda acc: fired.append(("success", acc.cookie)),
    )
    acc = sched.acquire()
    sched.release_neutral(acc)
    assert fired == []
    assert acc.held is False
    assert acc.disabled_reason is None
    assert acc.cooldown_until > time.monotonic()  # still rests


def test_on_success_fires_on_every_release_not_just_first():
    """The cookies view 「檢查：」 time must advance throughout a run.

    Regression for "cookies 還是沒有刷新": the success callback used to fire only
    the first time each cookie won in a run (a `_used_cookies` gate), so a long
    run froze every cookie's last_tested_at at its first-use time. It must now
    fire on EVERY successful release so the timestamp keeps advancing.
    """
    fired = []
    a1 = AccountState(cookie="c1", alias="A1")
    sched, _stop = _scheduler([a1], on_success=lambda acc: fired.append(acc.cookie))
    sched.release(a1, ok=True)
    sched.release(a1, ok=True)
    sched.release(a1, ok=True)
    assert fired == ["c1", "c1", "c1"]


def test_all_disabled_message_emitted_once():
    msgs = []
    a1 = AccountState(cookie="c1", alias="A1", disabled_reason="proxy_dead")
    sched, _stop = _scheduler([a1])
    sched._emit = lambda html: msgs.append(html)
    assert sched.acquire() is None
    assert sched.acquire() is None
    assert len([m for m in msgs if "都已禁用" in m]) == 1


def test_countdown_nonzero_throttled_zero_always_passes():
    q = queue.Queue()
    a1 = AccountState(cookie="c1", alias="A1")
    sched, _stop = _scheduler([a1], q=q)
    sched._emit_countdown(5)
    sched._emit_countdown(4)  # within 1s -> dropped
    sched._emit_countdown(0)  # zero always passes
    kinds = []
    while not q.empty():
        kinds.append(q.get_nowait().data)
    assert kinds == [5, 0]


# ── download_thread release semantics ────────────────────────────────────────

class _FakeQ:
    def __init__(self):
        self.events = []

    def put(self, ev):
        self.events.append(ev)


class _RecordingScheduler:
    def __init__(self, account):
        self._account = account
        self.calls = []

    def acquire(self):
        return self._account

    def release(self, account, ok=True):
        self.calls.append(("release", ok))

    def release_neutral(self, account):
        self.calls.append(("neutral", None))


def _make_thread(retry):
    t = download_thread.__new__(download_thread)
    t._q = _FakeQ()
    t._stop_event = threading.Event()
    t._pause_event = threading.Event()
    t._pause_event.set()
    t._pid_cookie_selection = {}
    t._current_account_local = threading.local()
    t.timelock = threading.Lock()
    t.download_time = datetime.datetime(2026, 1, 1)
    t._scheduler = _RecordingScheduler(AccountState(cookie="c1", alias="A1"))
    t._run_with_network_retry = retry
    t._download_pid_group = lambda pid, urls: []
    return t


def test_clean_run_releases_ok_true():
    t = _make_thread(lambda label, fn: (True, fn(), None))
    result, stop = t._download_pid_with_scheduler("10", ["u"])
    assert (result, stop) == ([], False)
    assert t._scheduler.calls == [("release", True)]


def test_retry_exhausted_without_stop_disables():
    t = _make_thread(lambda label, fn: (False, None, RuntimeError("x")))
    t._download_pid_with_scheduler("10", ["u"])
    assert t._scheduler.calls == [("release", False)]


def test_stop_during_retry_releases_neutral_not_disable():
    def retry(label, fn):
        t._stop_event.set()
        return False, None, None

    t = _make_thread(None)
    t._run_with_network_retry = retry
    t._download_pid_with_scheduler("10", ["u"])
    assert t._scheduler.calls == [("neutral", None)]


def test_non_network_exception_releases_neutral_and_propagates():
    def retry(label, fn):
        raise OSError("disk full")

    t = _make_thread(retry)
    try:
        t._download_pid_with_scheduler("10", ["u"])
        raised = False
    except OSError:
        raised = True
    assert raised
    assert t._scheduler.calls == [("neutral", None)]


# ── timechanged persistence ──────────────────────────────────────────────────

def test_emit_timechanged_pushes_event():
    t = _make_thread(lambda label, fn: (True, fn(), None))
    t.download_time = datetime.datetime(2026, 6, 13, 12, 0, 5)
    t._emit_timechanged()
    evs = [e for e in t._q.events if e.type == "timechanged"]
    assert evs and evs[-1].data == "2026-06-13 12:00:05"


def test_assign_persists_once_pool_does_not_emit_per_pid():
    """Timetag persistence is now up-front (assign_pid_timetags), once — the pool
    no longer emits a timechanged per PID."""
    t = _make_thread(lambda label, fn: (True, fn(), None))
    t._step4_pid_total = 1
    t._step4_pid_done = 0
    t._emit_phase = lambda *a: None
    t._maybe_flush_url_meta_periodically = lambda done: None
    t._download_pid_group = lambda pid, urls: []  # isolate the emit cadence
    t.assign_pid_timetags(["10"])
    assert sum(1 for e in t._q.events if e.type == "timechanged") == 1
    t._execute_downloads_pool([["10"]], {"10": ["u"]})
    assert sum(1 for e in t._q.events if e.type == "timechanged") == 1


def test_pool_stop_skips_remaining_batches_and_does_not_count_success():
    t = _make_thread(lambda label, fn: (True, fn(), None))
    t._step4_pid_total = 2
    t._step4_pid_done = 0
    t._emit_phase = lambda *a: None
    t._maybe_flush_url_meta_periodically = lambda done: None
    t._download_pid_with_scheduler = lambda pid, urls: ([], True)  # all disabled
    failed = t._execute_downloads_pool([["10"], ["20"]], {"10": ["u"], "20": ["u"]})
    assert failed == []  # stop result is not recorded as a no-failure success


# ── file mtime stamping ──────────────────────────────────────────────────────

def test_apply_download_mtime_sets_file_time(tmp_path):
    t = download_thread.__new__(download_thread)
    t.set_file_mtime = True
    f = tmp_path / "x.jpg"
    f.write_bytes(b"d")
    t._apply_download_mtime(str(f), "20260330_084355")
    expected = datetime.datetime(2026, 3, 30, 8, 43, 55).timestamp()
    assert abs(os.path.getmtime(f) - expected) < 2


def test_apply_download_mtime_disabled_noop(tmp_path):
    t = download_thread.__new__(download_thread)
    t.set_file_mtime = False
    f = tmp_path / "x.jpg"
    f.write_bytes(b"d")
    before = os.path.getmtime(f)
    t._apply_download_mtime(str(f), "20000101_000000")
    assert os.path.getmtime(f) == before
