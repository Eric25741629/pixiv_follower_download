"""Per-lane (per concurrent worker) progress for parallel combined mode.

Covers _LanePageQueue (translates the downloader's per-file "progress" into
slot-keyed "lane" events with an absolute page count) and the worker-side lane
lifecycle emission in combined_thread._run_concurrent.
"""
import datetime
import sys
from pathlib import Path
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import threading

from app.core.worker_event import WorkerEvent
from app.core.combined_progress_queues import _LaneProgressQueue
from app.core.thread_combined import combined_thread
from app.core.account_scheduler import AccountScheduler, AccountState


def _thread(tmp, workers=2, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("APPDATA", tmp)
    return combined_thread(
        q=Queue(), Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=tmp,
        single_thread_mode=True, download_path=tmp,
        download_time=datetime.datetime(1970, 1, 1),
        combined_workers=workers,
    )


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _no_throttle(monkeypatch):
    import app.core.combined_progress_queues as cpq
    monkeypatch.setattr(cpq, "_LANE_MIN_INTERVAL", 0)


def test_lane_progress_queue_accumulates_absolute_page_from_ctx(monkeypatch):
    _no_throttle(monkeypatch)
    q = Queue()
    ctx = {"slot": 1, "pid": "123", "alias": "cookieA", "total": 3, "page": 0}
    lq = _LaneProgressQueue(q, lambda: ctx)
    lq.put(WorkerEvent("progress", (1, 999)))
    lq.put(WorkerEvent("progress", (1, 999)))
    out = [q.get_nowait() for _ in range(q.qsize())]
    assert all(e.type == "lane" for e in out)
    assert [e.data["page"] for e in out] == [1, 2]
    assert out[0].data["slot"] == 1
    assert out[0].data["alias"] == "cookieA"
    assert out[0].data["pid"] == "123"
    assert out[0].data["total"] == 3
    assert out[0].data["state"] == "下載"


def test_lane_progress_queue_caps_page_at_total():
    q = Queue()
    ctx = {"slot": 0, "pid": "1", "alias": "A", "total": 2, "page": 0}
    lq = _LaneProgressQueue(q, lambda: ctx)
    for _ in range(5):
        lq.put(WorkerEvent("progress", (1, 0)))
    pages = [e.data["page"] for e in [q.get_nowait() for _ in range(q.qsize())]]
    assert max(pages) == 2  # never exceeds total


def test_lane_progress_queue_drops_progress_with_no_ctx():
    q = Queue()
    lq = _LaneProgressQueue(q, lambda: None)
    lq.put(WorkerEvent("progress", (1, 5)))
    assert q.empty()  # no active lane on this thread -> never hits overall bar


def test_lane_progress_queue_passes_through_non_progress():
    q = Queue()
    lq = _LaneProgressQueue(q, lambda: None)
    lq.put(WorkerEvent("output", "<p>hi</p>"))
    e = q.get_nowait()
    assert e.type == "output" and e.data == "<p>hi</p>"


def test_lane_progress_queue_throttles_intermediate_emits(monkeypatch):
    """Freeze insurance: rapid intermediate pages are coalesced, but the first
    page and the completion page always emit."""
    import app.core.combined_progress_queues as cpq
    monkeypatch.setattr(cpq, "_LANE_MIN_INTERVAL", 999)  # throttle all intermediate
    q = Queue()
    ctx = {"slot": 0, "pid": "1", "alias": "A", "total": 3, "page": 0}
    lq = _LaneProgressQueue(q, lambda: ctx)
    lq.put(WorkerEvent("progress", (1, 0)))  # page 1 — first emit, always shown
    lq.put(WorkerEvent("progress", (1, 0)))  # page 2 — intermediate, coalesced
    lq.put(WorkerEvent("progress", (1, 0)))  # page 3 == total — completion, always shown
    pages = [e.data["page"] for e in [q.get_nowait() for _ in range(q.qsize())]]
    assert pages == [1, 3]


def test_lane_progress_queue_isolates_concurrent_threads(monkeypatch):
    """The shared queue + thread-local ctx is the whole point: K workers sharing
    one downloader._q must not cross-attribute pages. Regression for the per-PID
    queue-swap race that the swap approach had."""
    _no_throttle(monkeypatch)
    q = Queue()
    tl = threading.local()
    lq = _LaneProgressQueue(q, lambda: getattr(tl, "ctx", None))
    barrier = threading.Barrier(2, timeout=5)

    def worker(slot, pid):
        tl.ctx = {"slot": slot, "pid": pid, "alias": f"c{slot}", "total": 3, "page": 0}
        barrier.wait()
        for _ in range(3):
            lq.put(WorkerEvent("progress", (1, 0)))

    t1 = threading.Thread(target=worker, args=(0, "A"))
    t2 = threading.Thread(target=worker, args=(1, "B"))
    t1.start(); t2.start(); t1.join(); t2.join()

    by_slot = {}
    for _ in range(q.qsize()):
        d = q.get_nowait().data
        by_slot.setdefault(d["slot"], []).append(d)
    assert sorted(by_slot) == [0, 1]
    for slot, items in by_slot.items():
        assert [d["page"] for d in items] == [1, 2, 3]          # independent counters
        assert all(d["pid"] == ("A" if slot == 0 else "B") for d in items)  # no cross-talk


def test_emit_lane_puts_partial_update(tmp_path, monkeypatch):
    t = _thread(str(tmp_path), workers=2, monkeypatch=monkeypatch)
    _drain(t._q)
    t._emit_lane(1, pid="9", alias="A", state="下載", page=2, total=5)
    e = _drain(t._q)[-1]
    assert e.type == "lane"
    assert e.data == {"slot": 1, "pid": "9", "alias": "A",
                      "state": "下載", "page": 2, "total": 5}


def test_emit_lane_noop_when_slot_none(tmp_path, monkeypatch):
    t = _thread(str(tmp_path), workers=2, monkeypatch=monkeypatch)
    _drain(t._q)
    t._emit_lane(None, pid="x")
    assert t._q.empty()


def test_run_concurrent_emits_lane_lifecycle_and_distinct_slots(tmp_path, monkeypatch):
    t = _thread(str(tmp_path), workers=2, monkeypatch=monkeypatch)
    a, b = AccountState("c1", "A"), AccountState("c2", "B")
    t._pause_event.set()
    sched = AccountScheduler([a, b], lambda: 0, t._pause_event, t._stop_event)
    t._scheduler = sched
    barrier = threading.Barrier(2, timeout=5)
    slots, slock = [], threading.Lock()

    def stub(pid, nq, *, slot=None, **kw):
        acc = sched.acquire()
        with slock:
            slots.append(slot)
        barrier.wait()              # force both workers in flight at once
        sched.release(acc, ok=True)
        return [], True

    t._process_one_pid_core = stub
    t.fetcher._mark_pid_processed = lambda pid: None
    t.downloader._emit_timechanged = lambda: None
    t._run_concurrent([("1", True), ("2", True)], 2, 2)

    evs = _drain(t._q)
    kinds = [e.type for e in evs]
    assert "lanes_init" in kinds
    init = next(e for e in evs if e.type == "lanes_init")
    assert init.data == {"count": 2}
    assert "lanes_clear" in kinds
    assert sorted(slots) == [0, 1]   # two distinct slots claimed concurrently


def test_lane_ctx_wiring_end_to_end(tmp_path, monkeypatch):
    """Drive the REAL _process_one_pid_core seam: slot claim -> ctx set
    (thread_combined) -> _LaneProgressQueue installed on the shared downloader._q
    -> downloader per-page progress -> slot-keyed lane events. The other tests
    stub _process_one_pid_core or hand-build the ctx; this exercises the actual
    wiring so a refactor that breaks the ctx set/read can't pass silently."""
    import app.core.combined_progress_queues as cpq
    monkeypatch.setattr(cpq, "_LANE_MIN_INTERVAL", 0)  # see every page event
    t = _thread(str(tmp_path), workers=1, monkeypatch=monkeypatch)
    a = AccountState("c1", "cookieA")
    t._pause_event.set()
    t._scheduler = AccountScheduler([a], lambda: 0, t._pause_event, t._stop_event)
    monkeypatch.setattr(t, "_download_only_urls",
                        lambda pid: ["http://x/a_p0.jpg", "http://x/a_p1.jpg"])
    monkeypatch.setattr(t, "_seed_pending_urls", lambda pid, urls: None)
    monkeypatch.setattr(t, "_mark_urls_done", lambda urls: None)
    monkeypatch.setattr(t.downloader, "_maybe_flush_exist_pid", lambda pid: None)

    def fake_dl(pid, urls):
        # Emit per-page progress on the installed queue, on the worker thread,
        # exactly as the real downloader does.
        for _ in urls:
            t.downloader._q.put(WorkerEvent("progress", (1, 99)))
        return []

    monkeypatch.setattr(t.downloader, "_download_pid_group", fake_dl)
    t._run_concurrent([("777", False)], 1, 1)

    lane_dl = [e.data for e in _drain(t._q)
               if e.type == "lane" and e.data.get("state") == "下載"
               and e.data.get("page", 0) > 0]
    assert lane_dl, "no lane download events came through the real ctx wiring"
    last = lane_dl[-1]
    assert last["slot"] == 0
    assert last["pid"] == "777"
    assert last["alias"] == "cookieA"
    assert last["page"] == 2 and last["total"] == 2


def test_sequential_path_passes_no_slot(tmp_path, monkeypatch):
    t = _thread(str(tmp_path), workers=1, monkeypatch=monkeypatch)
    seen = []

    def stub(pid, nq, *, slot=None, **kw):
        seen.append(slot)
        return [], True

    t._process_one_pid_core = stub
    t._process_one_pid("1", True)
    assert seen == [None]
