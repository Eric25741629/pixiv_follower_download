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


# ── _refresh_and_write_exist_pid ─────────────────────────────────────────────

def _stub_with_download_dir(tmp_path):
    t = _stub(tmp_path)
    # no files exist yet
    return t


def test_refresh_exist_pid_from_json(tmp_path):
    t = _stub_with_download_dir(tmp_path)
    pids = ["111", "222", "333"]
    (tmp_path / "exist_pid.json").write_text(
        json.dumps(pids), encoding="utf-8"
    )
    t._refresh_and_write_exist_pid()
    assert "111" in t.exist_pid
    assert "222" in t.exist_pid


def test_refresh_exist_pid_fallback_to_legacy_json(tmp_path):
    t = _stub_with_download_dir(tmp_path)
    pids = ["444", "555"]
    (tmp_path / "exist.json").write_text(
        json.dumps(pids), encoding="utf-8"
    )
    t._refresh_and_write_exist_pid()
    assert "444" in t.exist_pid


def test_refresh_exist_pid_empty_dir(tmp_path):
    t = _stub_with_download_dir(tmp_path)
    t._refresh_and_write_exist_pid()
    # No crash, exist_pid is a set (possibly empty)
    assert isinstance(t.exist_pid, set)


def test_refresh_exist_pid_writes_back_json(tmp_path):
    t = _stub_with_download_dir(tmp_path)
    pids = ["111", "222"]
    (tmp_path / "exist_pid.json").write_text(json.dumps(pids), encoding="utf-8")
    t._refresh_and_write_exist_pid()
    # Written back
    written = json.loads((tmp_path / "exist_pid.json").read_text(encoding="utf-8"))
    assert "111" in written
    assert "222" in written


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
    t._current_account = AccountState(
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
