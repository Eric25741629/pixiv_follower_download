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
    cookie = "c1"; proxy_url = None


def _retry_router(query_ret):
    """Build a _run_with_network_retry stand-in.

    When ``needs_query`` is True, _process_one_pid calls the wrapper twice:
    first for the meta query, then for the download. We route by call order
    instead of matching the (Chinese) label substring to stay encoding-proof.
    """
    state = {"n": 0}

    def fake_retry(label, fn):
        state["n"] += 1
        if state["n"] == 1:
            return query_ret  # query result, fixed
        return True, fn(), None  # download: actually run _download_pid_group

    return fake_retry


def test_clean_query_seeds_pending_then_marks_downloaded_and_closes_pid():
    """A freshly-queried PID with a clean download must: seed pending pages,
    flip them to downloaded, close the PID, and report genuine success."""
    t = _thread()
    db = t.fetcher._metadata_db
    # PIDs must be 4-12 digits to parse out of the URL (parse_pid_and_page_from_url).
    urls = ["https://i.pximg.net/img/55501_p0.jpg", "https://i.pximg.net/img/55501_p1.jpg"]

    # The query path is stubbed (no real get_download_url), so seed the
    # fetcher's url_meta with a page_count the way a real query would. This
    # is what _persist_pid_meta lands in artworks so the closed-view becomes
    # exact (v_complete_artworks needs a non-NULL page_count).
    t.fetcher.url_meta = {"55501": {"pagecount": 2, "like": 100, "tag": []}}

    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: None
    t._run_with_network_retry = _retry_router((True, urls, None))

    seen_pending = {"counts": None}

    def fake_download(pid, dl_urls):
        # At download time the pages must already be seeded as pending.
        seen_pending["counts"] = db.page_status_counts()
        return []  # no failures

    t.downloader._download_pid_group = fake_download

    failed = t._process_one_pid("55501", needs_query=True)

    assert failed == []
    assert t._last_pid_ok is True
    # Pending rows existed before the download ran.
    assert seen_pending["counts"].get("pending", 0) == 2
    # After a clean download they are flipped to downloaded.
    assert db.page_status_counts().get("downloaded", 0) == 2
    assert db.page_status_counts().get("pending", 0) == 0
    # And _maybe_flush_exist_pid ran, retiring the PID from future scans.
    assert "55501" in t.downloader.exist_pid
    # Stronger than exist_pid membership: the canonical store now treats this
    # PID as complete (page_count met) AND closed — meta was persisted at
    # success time, not deferred to _finalize.
    assert db.is_pid_complete("55501") is True
    assert db.is_pid_closed("55501") is True


def test_failed_query_does_not_retire_pid_and_marks_not_ok():
    """An exhausted query must leave _last_pid_ok False so run() keeps the
    PID pending; no pages get seeded and no download fires."""
    t = _thread()
    db = t.fetcher._metadata_db

    t._acquire_account = lambda: _Acc()
    released = {"ok": None}
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: released.__setitem__("ok", ok)
    t._run_with_network_retry = _retry_router((False, None, None))

    downloaded = {"called": False}
    t.downloader._download_pid_group = lambda pid, urls: downloaded.__setitem__("called", True) or []

    failed = t._process_one_pid("999", needs_query=True)

    assert failed == []
    assert t._last_pid_ok is False
    assert downloaded["called"] is False
    assert db.page_status_counts().get("pending", 0) == 0
    # A failed query (ok=False) must release the cookie as not-ok.
    assert released["ok"] is False


def test_partial_download_failure_keeps_pid_pending():
    """A partial download (some pages failed) must NOT mark urls done, NOT
    close the PID, and report _last_pid_ok False."""
    t = _thread()
    db = t.fetcher._metadata_db
    urls = ["https://i.pximg.net/img/44401_p0.jpg", "https://i.pximg.net/img/44401_p1.jpg"]

    t._acquire_account = lambda: _Acc()
    released = {"ok": None}
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: released.__setitem__("ok", ok)
    t._run_with_network_retry = _retry_router((True, urls, None))

    # _download_pid_group returns a non-empty failed list -> partial failure.
    t.downloader._download_pid_group = lambda pid, dl_urls: [[urls[1], "boom"]]
    t.downloader._maybe_flush_exist_pid = lambda pid: None

    failed = t._process_one_pid("44401", needs_query=True)

    assert failed  # propagated failure
    assert t._last_pid_ok is False
    # Pages were seeded pending, but NOT flipped to downloaded.
    assert db.page_status_counts().get("downloaded", 0) == 0
    assert db.page_status_counts().get("pending", 0) == 2
    assert not db.is_pid_closed("44401")
    # Ordinary page failures keep the PID pending but should not disable a
    # healthy cookie; this mirrors standalone Step 4's scheduler release path.
    assert released["ok"] is True


def test_partial_download_failure_still_refreshes_cookie_first_success():
    """Combined mode should not treat ordinary page failures as a dead cookie.

    Standalone Step 4 releases the scheduler as successful when the download
    callable returns a failed-page list; only proxy/network exhaustion disables
    the account. Combined mode needs the same release path so AccountScheduler's
    on_first_success callback refreshes auth.cookies_entries[].last_tested_at.
    """
    from app.core.account_scheduler import AccountScheduler, AccountState

    t = _thread()
    urls = ["https://i.pximg.net/img/66601_p0.jpg"]
    refreshed = []
    disabled = []

    acc = AccountState(cookie="c1", alias="A")
    scheduler = AccountScheduler(
        accounts=[acc],
        get_cooldown_avg=lambda: 0,
        pause_event=t._pause_event,
        stop_event=t._stop_event,
        on_success=lambda account: refreshed.append(account.cookie),
        on_disable=lambda account: disabled.append(account.cookie),
    )
    t._scheduler = scheduler
    # 真 scheduler 會打開匿名探測閘門；這裡測的是 release/refresh 語義，
    # 探測一律回退（否則單元測試會真的打 pixiv 網路）。
    t._probe_pid_anonymously = lambda pid: None
    t._run_with_network_retry = _retry_router((True, urls, None))
    t.downloader._download_pid_group = lambda pid, dl_urls: [[urls[0], "disk full"]]
    t.downloader._maybe_flush_exist_pid = lambda pid: None

    failed = t._process_one_pid("66601", needs_query=True)

    assert failed == [[urls[0], "disk full"]]
    assert refreshed == ["c1"]
    assert disabled == []
