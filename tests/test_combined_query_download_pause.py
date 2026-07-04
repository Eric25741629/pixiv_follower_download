"""查詢→下載緩衝 + per-lane acquire 倒數 (combined mode)."""
import os, sys, tempfile, datetime, threading
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from queue import Queue
from app.core.thread_combined import combined_thread, QUERY_TO_DOWNLOAD_PAUSE_RANGE
from app.core.account_scheduler import AccountScheduler, AccountState


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def _thread():
    path = tempfile.mkdtemp()
    return combined_thread(
        q=Queue(), Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=path,
        single_thread_mode=True, download_path=path,
        download_time=datetime.datetime(1970, 1, 1),
    )


class _Acc:
    cookie = "c1"; proxy_url = None; alias = "A"


def _stub_engines(t, urls):
    def fake_retry(label, fn):
        if "下載" in label:
            return True, fn(), None
        return True, list(urls), None
    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: None
    t._run_with_network_retry = fake_retry
    t.downloader._download_pid_group = lambda pid, u: []
    t.downloader._maybe_flush_exist_pid = lambda pid: None
    t._seed_pending_urls = lambda pid, u: None
    t._mark_urls_done = lambda u: None
    t._persist_pid_meta = lambda pid: None


# ── 查詢→下載 1-3 秒緩衝 ────────────────────────────────────────────────────
def test_query_then_download_pauses_one_to_three_seconds(monkeypatch):
    t = _thread()
    _stub_engines(t, ["https://x/123_p0.jpg"])
    waits = []
    monkeypatch.setattr(t, "_wait_interruptible", lambda s: waits.append(s) or True)

    t._process_one_pid("123", needs_query=True)

    lo, hi = QUERY_TO_DOWNLOAD_PAUSE_RANGE
    assert len(waits) == 1
    assert lo <= waits[0] <= hi


def test_download_only_pid_has_no_query_pause(monkeypatch):
    t = _thread()
    _stub_engines(t, [])
    t._download_only_urls = lambda pid: ["https://x/9_p0.jpg"]
    waits = []
    monkeypatch.setattr(t, "_wait_interruptible", lambda s: waits.append(s) or True)

    t._process_one_pid("9", needs_query=False)

    assert waits == []


def test_query_with_no_urls_has_no_pause(monkeypatch):
    t = _thread()
    _stub_engines(t, [])
    waits = []
    monkeypatch.setattr(t, "_wait_interruptible", lambda s: waits.append(s) or True)

    t._process_one_pid("123", needs_query=True)

    assert waits == []


# ── per-lane acquire 倒數 ───────────────────────────────────────────────────
def test_make_lane_wait_callback_none_for_sequential():
    assert _thread()._make_lane_wait_callback(None) is None


def test_make_lane_wait_callback_emits_deduped_lane_waits():
    t = _thread()
    cb = t._make_lane_wait_callback(2)
    for r in (5, 5, 4, 4, 0):
        cb(r)
    lanes = [e.data for e in _drain(t._q) if e.type == "lane"]
    assert [(d["slot"], d["state"], d["wait"]) for d in lanes] == [
        (2, "等待", 5), (2, "等待", 4), (2, "等待", 0),
    ]


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ── scheduler: on_wait 是 per-caller 的等待觀察者 ───────────────────────────
def test_scheduler_acquire_reports_wait_to_caller():
    a = AccountState("c1", "A")
    pause, stop = threading.Event(), threading.Event()
    pause.set()
    sched = AccountScheduler([a], lambda: 0, pause, stop)
    a.cooldown_until = 0.0  # ready now
    sched._next_emit_at = 0.0
    seen = []
    acc = sched.acquire(on_wait=seen.append)
    assert acc is a
    assert seen[-1] == 0  # pickup always closes with 0


def test_scheduler_acquire_ticks_caller_during_cooldown():
    import time as _time
    a = AccountState("c1", "A")
    pause, stop = threading.Event(), threading.Event()
    pause.set()
    sched = AccountScheduler([a], lambda: 0, pause, stop)
    a.cooldown_until = _time.monotonic() + 30
    seen = []

    def on_wait(r):
        seen.append(r)
        stop.set()  # exit after the first observed tick

    assert sched.acquire(on_wait=on_wait) is None
    assert seen[0] > 0      # the caller saw its own remaining seconds
    assert seen[-1] == 0    # and a closing 0 on exit
