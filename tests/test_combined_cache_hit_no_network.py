import os, sys, tempfile, datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from queue import Queue
from app.core.thread_combined import combined_thread


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    """Point APPDATA at a tmp dir so construction never reads the real
    %APPDATA% cookie-requirement JSON / history / production DB (slow)."""
    monkeypatch.setenv("APPDATA", str(tmp_path))


def test_download_only_pid_does_not_query():
    path = tempfile.mkdtemp()
    t = combined_thread(
        q=Queue(), Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=path,
        single_thread_mode=True, download_path=path,
        download_time=datetime.datetime(1970, 1, 1),
    )
    db = t.fetcher._metadata_db
    db.upsert_page("777", 0, status="pending", url="https://x/777_p0.jpg")

    # Build the work lists once; this caches the per-PID pending urls
    # (the single v_pending_pages scan that _download_only_urls reuses).
    query_pids, download_only = t._build_work_lists()
    assert "777" in download_only

    queried = {"n": 0}

    class _Acc:
        cookie = "c1"; proxy_url = None

    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True: None

    def fake_retry(label, fn):
        # Only the network query path increments queried; the download path
        # ("下載" label) must actually run fn() so _download_pid_group fires.
        if "下載" in label:
            return True, fn(), None
        queried["n"] += 1
        return True, [], None

    t._run_with_network_retry = fake_retry
    got = {"urls": None}
    t.downloader._download_pid_group = lambda pid, urls: got.__setitem__("urls", urls) or []
    t.downloader._maybe_flush_exist_pid = lambda pid: None

    t._process_one_pid("777", needs_query=False)

    assert queried["n"] == 0  # no network query for download-only PID
    assert got["urls"] == ["https://x/777_p0.jpg"]
