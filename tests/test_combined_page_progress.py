import datetime
import os
import sys
import tempfile
from queue import Queue

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.thread_combined import combined_thread


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    """Keep combined worker construction away from the real APPDATA cache."""
    monkeypatch.setenv("APPDATA", str(tmp_path))


class _Acc:
    cookie = "c1"
    proxy_url = None


def _thread():
    path = tempfile.mkdtemp()
    q = Queue()
    t = combined_thread(
        q=q,
        Author_list=[],
        Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}],
        exist_pid=set(),
        ban_tag=[],
        must_tag=[],
        like_num=0,
        no_to_check=[],
        base_path=path,
        single_thread_mode=True,
        download_path=path,
        download_time=datetime.datetime(1970, 1, 1),
    )
    return t, q


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


def test_process_one_pid_emits_page_progress_without_zero_total_progress():
    t, q = _thread()
    urls = [
        "https://i.pximg.net/img/55501_p0.jpg",
        "https://i.pximg.net/img/55501_p1.jpg",
    ]

    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: None

    state = {"calls": 0}

    def fake_retry(label, fn):
        state["calls"] += 1
        if state["calls"] == 1:
            return True, urls, None
        return True, fn(), None

    t._run_with_network_retry = fake_retry
    t.downloader._dispatch_download = lambda *args, **kwargs: -1

    failed = t._process_one_pid("55501", needs_query=True)

    events = _drain(q)
    assert failed == []
    assert [e.data for e in events if e.type == "page_progress"] == [
        {"delta": 0, "total": 2, "pid": "55501"},
        {"delta": 1, "total": 2, "pid": "55501"},
        {"delta": 1, "total": 2, "pid": "55501"},
    ]
    assert [
        e.data
        for e in events
        if e.type == "progress" and isinstance(e.data, tuple) and e.data[1] == 0
    ] == []


def test_run_keeps_pid_progress_separate_from_page_progress():
    t, q = _thread()
    pids = ["11111", "22222"]

    t._build_work_lists = lambda: (pids, [])
    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: None
    t.fetcher._mark_pid_processed = lambda pid: None
    t.downloader._dispatch_download = lambda *args, **kwargs: -1

    def fake_retry(label, fn):
        if "下載" in label:
            return True, fn(), None
        pid = label.split()[1]
        return True, [f"https://i.pximg.net/img/{pid}_p0.jpg"], None

    t._run_with_network_retry = fake_retry

    t.run()

    events = _drain(q)
    assert [e.data for e in events if e.type == "progress"] == [
        (0, 2),
        (1, 2),
        (1, 2),
    ]
    assert [e.data for e in events if e.type == "page_progress"] == [
        {"delta": 0, "total": 1, "pid": "11111"},
        {"delta": 1, "total": 1, "pid": "11111"},
        {"delta": 0, "total": 1, "pid": "22222"},
        {"delta": 1, "total": 1, "pid": "22222"},
    ]


def test_run_clears_page_progress_when_pid_has_no_download_urls():
    t, q = _thread()
    pids = ["11111", "22222"]

    t._build_work_lists = lambda: (pids, [])
    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: None
    t.fetcher._mark_pid_processed = lambda pid: None
    t.downloader._dispatch_download = lambda *args, **kwargs: -1

    def fake_retry(label, fn):
        if "下載" in label:
            return True, fn(), None
        pid = label.split()[1]
        if pid == "11111":
            return True, [f"https://i.pximg.net/img/{pid}_p0.jpg"], None
        return True, [], None

    t._run_with_network_retry = fake_retry

    t.run()

    events = _drain(q)
    assert [e.data for e in events if e.type == "page_progress"] == [
        {"delta": 0, "total": 1, "pid": "11111"},
        {"delta": 1, "total": 1, "pid": "11111"},
        None,
    ]
