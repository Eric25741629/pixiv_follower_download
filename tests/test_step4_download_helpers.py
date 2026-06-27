"""Tests for download_thread helper methods (Phase 10 refactoring)."""
from pathlib import Path
import sys
import json
import threading
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


class _Signal:
    def emit(self, *_):
        pass


def _stub(tmp_path=None):
    t = download_thread.__new__(download_thread)
    t._output = _Signal()
    t.url_meta = {}
    t.url_meta_path = str(tmp_path / "all_url_meta.json") if tmp_path else "/tmp/meta.json"
    t.path = str(tmp_path) if tmp_path else "/tmp"
    t.exist_pid = set()
    t.exist_json_path = str(tmp_path / "exist_pid.json") if tmp_path else "/tmp/exist_pid.json"
    t.legacy_exist_json_path = str(tmp_path / "exist.json") if tmp_path else "/tmp/exist.json"
    t.exist_txt_path = str(tmp_path / "existPID.txt") if tmp_path else "/tmp/existPID.txt"
    t.download_path = str(tmp_path) if tmp_path else "/tmp"
    t._attempted_urls = set()
    t._attempted_urls_lock = threading.Lock()
    t._completed_urls = set()
    t._completed_urls_lock = threading.Lock()
    t.allurl = []
    t.q = Queue()
    return t


# ── _finalize_downloads ──────────────────────────────────────────────────────

def test_finalize_downloads_empty_results(tmp_path):
    t = _stub(tmp_path)
    (tmp_path / "all_url.txt").write_text("", encoding="utf-8")
    remaining = t._finalize_downloads([])
    assert isinstance(remaining, list)


def test_finalize_downloads_failed_urls_written(tmp_path):
    t = _stub(tmp_path)
    (tmp_path / "all_url.txt").write_text("", encoding="utf-8")
    # Simulate one failed URL result
    failed_nested = [["https://i.pximg.net/fail_p0.jpg", "404"]]
    t._finalize_downloads(failed_nested)
    err_file = tmp_path / "err_url.txt"
    assert err_file.exists()
    content = err_file.read_text(encoding="utf-8")
    assert "fail_p0.jpg" in content


def test_finalize_downloads_zero_items_ignored(tmp_path):
    t = _stub(tmp_path)
    (tmp_path / "all_url.txt").write_text("", encoding="utf-8")
    # 0 values should be filtered
    failed_nested = [[0, 0]]
    remaining = t._finalize_downloads(failed_nested)
    assert isinstance(remaining, list)


# ── _sync_exist_pid_to_db ────────────────────────────────────────────────────

def _stub_with_download_dir(tmp_path):
    t = _stub(tmp_path)
    # no files exist yet
    return t


def test_sync_exist_pid_writes_to_db(tmp_path):
    from app.core.metadata_db import MetadataDB
    t = _stub_with_download_dir(tmp_path)
    t.exist_pid = {"11100001", "22200001"}
    t._metadata_db = MetadataDB(str(tmp_path))
    t._sync_exist_pid_to_db()
    assert t._metadata_db.is_downloaded("11100001") is True
    assert t._metadata_db.is_downloaded("22200001") is True


def test_sync_exist_pid_no_db_does_not_raise(tmp_path):
    t = _stub_with_download_dir(tmp_path)
    t.exist_pid = {"11100001"}
    t._metadata_db = None
    t._sync_exist_pid_to_db()  # must not raise
    assert isinstance(t.exist_pid, set)


def test_sync_exist_pid_empty_set_is_noop(tmp_path):
    t = _stub_with_download_dir(tmp_path)
    t.exist_pid = set()
    t._metadata_db = None
    t._sync_exist_pid_to_db()
    assert isinstance(t.exist_pid, set)


# ── _group_urls_by_pid ────────────────────────────────────────────────────────

def test_group_urls_by_pid_basic():
    t = _stub()
    urls = [
        "https://i.pximg.net/img/123456_p0.jpg",
        "https://i.pximg.net/img/123456_p1.jpg",
        "https://i.pximg.net/img/789012_p0.jpg",
    ]
    pid_order, groups = t._group_urls_by_pid(urls)
    assert len(pid_order) == 2
    assert len(groups["123456"]) == 2
    assert len(groups["789012"]) == 1


def test_group_urls_by_pid_preserves_order():
    t = _stub()
    urls = [
        "https://i.pximg.net/img/333_p0.jpg",
        "https://i.pximg.net/img/111_p0.jpg",
        "https://i.pximg.net/img/222_p0.jpg",
    ]
    pid_order, _ = t._group_urls_by_pid(urls)
    assert pid_order[0] == "333"
    assert pid_order[1] == "111"
    assert pid_order[2] == "222"


def test_download_pid_group_uses_account_proxy_session(monkeypatch):
    """When _current_account is set, _download_pid_group builds a session via make_session."""
    import requests
    from app.core import thread_download as tdl
    from app.core import pixiv_api
    from app.core.account_scheduler import AccountState

    captured = {"proxy": "NOT_CALLED"}
    real_make = pixiv_api.make_session

    def spy_make(proxy_url=None):
        captured["proxy"] = proxy_url
        return real_make(proxy_url)

    monkeypatch.setattr(pixiv_api, "make_session", spy_make)

    t = tdl.download_thread.__new__(tdl.download_thread)
    t._stop_event = __import__("threading").Event()
    t._pause_event = __import__("threading").Event()
    t._pause_event.set()
    t._q = __import__("queue").Queue()
    t.q = __import__("queue").Queue()
    t._attempted_urls = set()
    t._attempted_urls_lock = __import__("threading").Lock()
    t._current_account_local = __import__("threading").local()
    t._current_account_local.account = AccountState(
        cookie="test_cookie", alias="A1", proxy_url="http://1.2.3.4:8080"
    )
    t.exist_pid = set()
    t.pid_max = 0
    t.pid_now = 0
    t._stop_after_group = False
    t._sleep_within_pid = lambda pid: None
    t.gif_or_jpg = lambda u, session=None: -1  # bypass actual download
    t._scheduler = object()  # truthy

    failed = t._download_pid_group("777", ["https://i.pximg.net/img-original/img/1/777_p0.png"])

    assert captured["proxy"] == "http://1.2.3.4:8080"
    assert isinstance(failed, list)


def test_gif_download_propagates_proxy_error(monkeypatch):
    """ProxyError from the inner http.get must propagate so scheduler can disable."""
    import pytest
    import requests
    import datetime
    from app.core import thread_download as tdl

    t = tdl.download_thread.__new__(tdl.download_thread)
    t._stop_event = __import__("threading").Event()
    t._pause_event = __import__("threading").Event()
    t._pause_event.set()
    t._q = __import__("queue").Queue()
    t.q = __import__("queue").Queue()
    t.cookies = ""
    t.cookie_pool = []
    t._cookie_alias_map = {}
    t._pid_cookie_selection = {}
    t._pid_cookie_used = {}
    t._cookie_usage_counts = {"step4": {}}
    t.url_meta = {}
    t.agent = "agent"
    t.download_path = "."
    t.download_time = datetime.datetime.now()
    t.timelock = __import__("threading").Lock()
    t.notag = False
    t.notime = False

    # Stub helpers that require network/full state so the test focuses on the
    # ProxyError propagation path inside gif_download.
    monkeypatch.setattr(t, "_resolve_pid_and_cookie",
                        lambda url, source="step4": ("777", "", None))
    monkeypatch.setattr(t, "_load_artwork_metadata",
                        lambda pid, pid_cookie: ([], 0, 1, None))
    monkeypatch.setattr(t, "_build_artwork_headers",
                        lambda *a, **kw: {"User-Agent": "agent"})
    monkeypatch.setattr(t, "_mark_gif_cookie_usage",
                        lambda *a, **kw: None)

    class FailGet:
        def get(self, *a, **kw):
            raise requests.exceptions.ProxyError("dead proxy")

    with pytest.raises(requests.exceptions.ProxyError):
        t.gif_download(
            "https://i.pximg.net/img-original/img/1/777_ugoira0.zip",
            session=FailGet(),
        )


# ── completed-vs-attempted marking (data-loss regression, B1/B2) ──────────────

def _stub_with_pending_db(tmp_path, url, pid):
    from app.core.metadata_db import MetadataDB
    t = _stub(tmp_path)
    (tmp_path / "all_url.txt").write_text("", encoding="utf-8")
    t.allurl = [url]
    t._metadata_db = MetadataDB(str(tmp_path))
    t._metadata_db.upsert_pending_urls([(url, pid)])
    return t


def test_attempted_but_failed_url_stays_pending_not_downloaded(tmp_path):
    """A page that was attempted but never confirmed on disk (network-retry
    exhausted -> empty fail list) must NOT be marked downloaded and must stay in
    the remaining set. Regression for the attempted==completed data-loss bug."""
    url = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/96000001_p0.jpg"
    t = _stub_with_pending_db(tmp_path, url, "96000001")
    # URL was attempted (progress advanced) but NOT completed, and the
    # network-exhaustion path produced no fail record (empty failed list).
    t._attempted_urls = {url}
    remaining = t._finalize_downloads([])
    assert url in remaining, "exhausted URL must stay queued for retry"
    assert t._metadata_db.pending_url_count() == 1, "page must stay pending"


def test_stop_interrupted_url_stays_pending_not_downloaded(tmp_path):
    """A page handed back via the stop-queue (user pressed Stop before the fetch)
    must NOT be marked downloaded. Regression for 'a stopped loop must not mark
    unattempted items done'."""
    url = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/96000002_p0.jpg"
    t = _stub_with_pending_db(tmp_path, url, "96000002")
    t._attempted_urls = {url}
    t.q.put(url)  # gif_or_jpg's stop path hands the URL back here
    remaining = t._finalize_downloads([])
    assert url in remaining
    assert t._metadata_db.pending_url_count() == 1


def test_completed_url_is_marked_downloaded(tmp_path):
    """Positive case: a confirmed-on-disk URL IS marked downloaded and drops out
    of the remaining set."""
    url = "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/96000003_p0.jpg"
    t = _stub_with_pending_db(tmp_path, url, "96000003")
    t._record_completed(url)
    remaining = t._finalize_downloads([])
    assert url not in remaining
    assert t._metadata_db.pending_url_count() == 0
    assert t._metadata_db.url_row_count() == 1
