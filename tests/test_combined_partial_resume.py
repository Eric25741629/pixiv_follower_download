"""Partial-download resume: a stopped/failed PID persists its completed pages
per-page, and a re-run skips those pages instead of re-downloading (and
duplicating under a new timetag)."""
import datetime
import os
import sys
import tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from queue import Queue
from app.core.thread_combined import combined_thread


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


def _retry_router(query_ret):
    state = {"n": 0}

    def fake_retry(label, fn):
        state["n"] += 1
        if state["n"] == 1:
            return query_ret
        return True, fn(), None

    return fake_retry


def _urls(pid, n):
    return [f"https://i.pximg.net/img/{pid}_p{i}.jpg" for i in range(n)]


def test_stop_mid_pid_persists_completed_pages():
    """Stop after page 0 finished: p0 must land as downloaded in the DB, the
    rest stay pending, and the PID stays open for the next run."""
    t = _thread()
    db = t.fetcher._metadata_db
    urls = _urls("77701", 3)
    t.fetcher.url_meta = {"77701": {"pagecount": 3, "like": 100, "tag": []}}
    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1: None
    t._run_with_network_retry = _retry_router((True, urls, None))

    def fake_download(pid, dl_urls):
        t.downloader._record_completed(dl_urls[0])  # p0 confirmed on disk
        t._stop_event.set()                          # user hits 中止
        return []                                    # stop -> empty fail list

    t.downloader._download_pid_group = fake_download

    failed = t._process_one_pid("77701", needs_query=True)

    assert failed == []
    assert t._last_pid_ok is False
    counts = db.page_status_counts()
    assert counts.get("downloaded", 0) == 1
    assert counts.get("pending", 0) == 2
    assert not db.is_pid_closed("77701")


def test_partial_failure_persists_completed_pages():
    """p0 done, p1 failed, p2 unattempted -> only p0 flips to downloaded."""
    t = _thread()
    db = t.fetcher._metadata_db
    urls = _urls("77702", 3)
    t.fetcher.url_meta = {"77702": {"pagecount": 3, "like": 100, "tag": []}}
    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1: None
    t._run_with_network_retry = _retry_router((True, urls, None))

    def fake_download(pid, dl_urls):
        t.downloader._record_completed(dl_urls[0])
        return [[dl_urls[1], "boom"]]

    t.downloader._download_pid_group = fake_download
    t.downloader._maybe_flush_exist_pid = lambda pid: None

    failed = t._process_one_pid("77702", needs_query=True)

    assert failed == [[urls[1], "boom"]]
    assert t._last_pid_ok is False
    counts = db.page_status_counts()
    assert counts.get("downloaded", 0) == 1
    assert counts.get("pending", 0) == 2
    assert not db.is_pid_closed("77702")


def test_requery_skips_already_downloaded_pages():
    """A resumed query-path PID must download only the still-pending pages."""
    t = _thread()
    db = t.fetcher._metadata_db
    urls = _urls("77703", 3)
    # Previous run: all seeded, p0 completed.
    db.upsert_pending_urls([(u, "77703") for u in urls])
    db.mark_urls_done([urls[0]])

    t.fetcher.url_meta = {"77703": {"pagecount": 3, "like": 100, "tag": []}}
    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1: None
    t._run_with_network_retry = _retry_router((True, urls, None))

    seen = {"urls": None}

    def fake_download(pid, dl_urls):
        seen["urls"] = list(dl_urls)
        return []

    t.downloader._download_pid_group = fake_download

    failed = t._process_one_pid("77703", needs_query=True)

    assert failed == []
    assert t._last_pid_ok is True
    assert seen["urls"] == urls[1:]  # p0 skipped
    counts = db.page_status_counts()
    assert counts.get("downloaded", 0) == 3
    assert counts.get("pending", 0) == 0
    assert db.is_pid_complete("77703") is True
    assert db.is_pid_closed("77703") is True


def test_requery_all_pages_already_downloaded_closes_pid_without_download():
    """Every page already on disk from a previous partial run: no download at
    all, but the success bookkeeping (meta persist + close) still happens."""
    t = _thread()
    db = t.fetcher._metadata_db
    urls = _urls("77704", 2)
    db.upsert_pending_urls([(u, "77704") for u in urls])
    db.mark_urls_done(urls)

    t.fetcher.url_meta = {"77704": {"pagecount": 2, "like": 100, "tag": []}}
    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1: None
    t._run_with_network_retry = _retry_router((True, urls, None))

    called = {"download": False}
    t.downloader._download_pid_group = lambda pid, dl_urls: called.__setitem__("download", True) or []

    failed = t._process_one_pid("77704", needs_query=True)

    assert failed == []
    assert t._last_pid_ok is True
    assert called["download"] is False
    assert db.is_pid_complete("77704") is True
    assert db.is_pid_closed("77704") is True
    assert "77704" in t.downloader.exist_pid


def test_downloaded_page_indices_helper(tmp_path):
    from app.core.metadata_db import MetadataDB
    db = MetadataDB(str(tmp_path / "m.sqlite3"))
    urls = _urls("88801", 3)
    db.upsert_pending_urls([(u, "88801") for u in urls])
    db.mark_urls_done([urls[0], urls[2]])
    assert db.downloaded_page_indices("88801") == {0, 2}
    assert db.downloaded_page_indices("99999") == set()
