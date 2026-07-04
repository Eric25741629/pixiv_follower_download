import os, sys, tempfile, datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from queue import Queue
from unittest import mock
from app.core.thread_combined import combined_thread


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    """Point APPDATA at a tmp dir so construction never reads the real
    %APPDATA% cookie-requirement JSON / history / production DB (slow)."""
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


def test_one_acquire_release_per_pid_covers_query_and_download():
    t = _thread()
    calls = {"acquire": 0, "release": 0, "query": 0, "download": []}

    class _Acc:
        cookie = "c1"; proxy_url = None

    def fake_acquire():
        calls["acquire"] += 1
        return _Acc()

    def fake_release(acc, ok=True, work_units=1, pages=0):
        calls["release"] += 1

    def fake_retry(label, fn):
        # Query is the bare label "PID 123"; download is "PID 123 下載".
        # Both now route through _run_with_network_retry; the download call
        # must actually invoke fn() so _download_pid_group runs.
        if "下載" in label:
            return True, fn(), None
        calls["query"] += 1
        return True, ["https://x/123_p0.jpg"], None

    def fake_download(pid, urls):
        calls["download"].append((pid, tuple(urls)))
        return []  # no failures

    t._acquire_account = fake_acquire
    t._release_account = fake_release
    t._run_with_network_retry = fake_retry
    t.downloader._download_pid_group = fake_download
    t.downloader._maybe_flush_exist_pid = lambda pid: None
    t._seed_pending_urls = lambda pid, urls: None
    t._mark_urls_done = lambda urls: None

    failed = t._process_one_pid("123", needs_query=True)

    assert calls["acquire"] == 1
    assert calls["release"] == 1
    assert calls["query"] == 1
    assert calls["download"] == [("123", ("https://x/123_p0.jpg",))]
    assert failed == []
    assert t._last_pid_ok is True
