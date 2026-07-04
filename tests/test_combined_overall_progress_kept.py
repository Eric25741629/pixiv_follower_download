"""Regression: combined mode must NOT let the fetcher blank the 整體進度 bar.

Root cause of "整體進度 shows when a PID finishes but disappears the moment the
next PID starts": the fetcher's get_download_url calls _step3_advance_progress
once per PID, emitting WorkerEvent("progress", (1, fetcher.pid_max)). In combined
mode fetcher.pid_max is 0 (its run()/_load_and_filter_pid_list never runs), so
that (1, 0) reaches MainView.update_progress and, because total <= 0, blanks the
overall bar on every query. combined owns overall progress (one tick per PID in
run()), so it must drop the fetcher's progress events while querying.
"""
import os
import sys
import datetime
import tempfile
from queue import Queue

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.thread_combined import combined_thread, _DropOverallProgressQueue
from app.core.worker_event import WorkerEvent


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
    cookie = "c1"
    proxy_url = None


# ── the wrapper itself ────────────────────────────────────────────────────────

def test_drop_queue_drops_progress_passes_everything_else():
    real = Queue()
    wrapped = _DropOverallProgressQueue(real)
    wrapped.put(WorkerEvent("progress", (1, 0)))     # the fetcher's spurious emit
    wrapped.put(WorkerEvent("output", "<p>hi</p>"))
    wrapped.put(WorkerEvent("page_progress", {"delta": 1, "total": 5, "pid": "1"}))
    wrapped.put(WorkerEvent("countdown", 3))
    kinds = []
    while not real.empty():
        kinds.append(real.get_nowait().type)
    assert "progress" not in kinds
    assert kinds == ["output", "page_progress", "countdown"]


# ── wired into _process_one_pid's query leg ───────────────────────────────────

def test_query_does_not_emit_overall_progress(monkeypatch):
    """While a PID is queried, the fetcher's overall 'progress' emit must be
    dropped (else it blanks 整體進度), but ordinary output must pass through."""
    t = _thread()
    real_q = t._q

    # Precondition that makes the bug possible: combined never sets the
    # fetcher's pid_max, so its per-PID progress emit carries total == 0.
    assert t.fetcher.pid_max == 0

    def fake_get_download_url(*a, **k):
        # Mimic _step3_advance_progress + a normal log line, via the fetcher's
        # _q (which _process_one_pid must have swapped to the drop-queue).
        t.fetcher._q.put(WorkerEvent("progress", (1, t.fetcher.pid_max)))
        t.fetcher._q.put(WorkerEvent("output", "<p>queried</p>"))
        return ["https://i.pximg.net/img/55502_p0.jpg"]

    t.fetcher.get_download_url = fake_get_download_url
    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1: None
    # Router that actually runs both legs so the queue swap is exercised.
    t._run_with_network_retry = lambda label, fn: (True, fn(), None)
    t.downloader._download_pid_group = lambda pid, urls: []

    t._process_one_pid("55502", needs_query=True)

    kinds = []
    while not real_q.empty():
        kinds.append(real_q.get_nowait().type)
    assert "progress" not in kinds          # overall bar never blanked
    assert "output" in kinds                # but the log line survived

    # And the fetcher's queue was restored to the real queue afterwards.
    assert t.fetcher._q is real_q
