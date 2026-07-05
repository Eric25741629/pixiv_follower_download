"""acquire 前匿名探測管線 (combined mode)：探測 → 免帳號 settle / work_units=0。"""
import os, sys, tempfile, datetime, time, threading
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from queue import Queue
import pixiv_api
from app.core import thread_url_fetch
from app.core.thread_combined import combined_thread, PROBE_MAX_CONCURRENCY
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


def _enable_probe(t):
    # 探測閘門只檢查 scheduler 非 None（真正的 acquire 都被測試 stub 掉）。
    t._scheduler = object()


class _Acc:
    cookie = "c1"; proxy_url = None; alias = "A"


VALID_INFO = [["tag"], 100, 2,
              "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/123_p0.jpg",
              None, None, "u1", "name"]
BLOCKED_INFO = [[], 0, 0, "None", None, None, None, None]


# ── _probe_pid_anonymously ──────────────────────────────────────────────────
def test_probe_gate_off_without_scheduler(monkeypatch):
    t = _thread()
    monkeypatch.setattr(pixiv_api, "Pixiv_info",
                        lambda *a, **k: pytest.fail("must not hit network"))
    assert t._probe_pid_anonymously("123") is None


def test_probe_valid_stashes_meta(monkeypatch):
    t = _thread()
    _enable_probe(t)
    t.fetcher._cookie_requirement_map = {}
    monkeypatch.setattr(pixiv_api, "Pixiv_info", lambda *a, **k: list(VALID_INFO))
    assert t._probe_pid_anonymously("123") == "stashed"
    assert t.fetcher._probe_info_results["123"] == VALID_INFO
    assert t.fetcher._cookie_requirement_map["123"] is False


def test_probe_blocked_marks_needs_cookie(monkeypatch):
    t = _thread()
    _enable_probe(t)
    t.fetcher._cookie_requirement_map = {}
    monkeypatch.setattr(pixiv_api, "Pixiv_info", lambda *a, **k: list(BLOCKED_INFO))
    assert t._probe_pid_anonymously("123") == "needs_cookie"
    assert "123" in t.fetcher._probe_requires_cookie
    assert "123" not in t.fetcher._probe_info_results
    assert t.fetcher._cookie_requirement_map["123"] is True


def test_probe_error_falls_back_and_404_stashes(monkeypatch):
    t = _thread()
    _enable_probe(t)
    monkeypatch.setattr(pixiv_api, "Pixiv_info", lambda *a, **k: ["error"])
    assert t._probe_pid_anonymously("1") is None
    monkeypatch.setattr(pixiv_api, "Pixiv_info", lambda *a, **k: [404])
    assert t._probe_pid_anonymously("2") == "stashed"
    assert t.fetcher._probe_info_results["2"] == [404]


def test_probe_exception_falls_back(monkeypatch):
    t = _thread()
    _enable_probe(t)
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(pixiv_api, "Pixiv_info", boom)
    assert t._probe_pid_anonymously("123") is None


def test_probe_skipped_on_fresh_cache(monkeypatch):
    t = _thread()
    _enable_probe(t)
    monkeypatch.setattr(t.fetcher, "_get_meta", lambda pid: {"x": 1})
    monkeypatch.setattr(t.fetcher, "_step3_cache_is_fresh", lambda c: True)
    monkeypatch.setattr(pixiv_api, "Pixiv_info",
                        lambda *a, **k: pytest.fail("cache-fresh must not probe"))
    assert t._probe_pid_anonymously("123") is None


def test_probe_concurrency_cap_is_eight():
    assert PROBE_MAX_CONCURRENCY == 8
    t = _thread()
    assert t._probe_semaphore._initial_value == 8


# ── fetcher 消費 stash / 跳過匿名段 ─────────────────────────────────────────
def test_fetch_artwork_info_consumes_stash(monkeypatch):
    t = _thread()
    t.fetcher._probe_info_results["123"] = list(VALID_INFO)
    monkeypatch.setattr(thread_url_fetch, "Pixiv_info",
                        lambda *a, **k: pytest.fail("stash hit must not refetch"))
    out = t.fetcher._step3_fetch_artwork_info(
        "https://www.pixiv.net/artworks/123", "UA", "123", "", None)
    assert out == VALID_INFO
    assert "123" not in t.fetcher._probe_info_results  # 一次性消費


def test_fetch_artwork_info_passes_skip_flag(monkeypatch):
    t = _thread()
    seen = {}
    def fake(url, Agent=None, cookie=None, session=None, skip_no_cookie=False):
        seen["skip"] = skip_no_cookie
        return list(VALID_INFO)
    monkeypatch.setattr(thread_url_fetch, "Pixiv_info", fake)
    t.fetcher._probe_requires_cookie = {"123"}
    t.fetcher._step3_fetch_artwork_info("u", "UA", "123", "ck", None)
    assert seen["skip"] is True
    t.fetcher._step3_fetch_artwork_info("u", "UA", "999", "ck", None)
    assert seen["skip"] is False


# ── Pixiv_info skip_no_cookie ───────────────────────────────────────────────
class _FakeResp:
    status_code = 404
    text = ""


class _FakeSession:
    def __init__(self):
        self.calls = []
    def get(self, url, headers=None, timeout=None):
        self.calls.append(dict(headers or {}))
        return _FakeResp()


def test_pixiv_info_skip_no_cookie_sends_single_cookie_request():
    sess = _FakeSession()
    out = pixiv_api.Pixiv_info("https://www.pixiv.net/artworks/123", Agent="UA",
                               cookie="ck", session=sess, skip_no_cookie=True)
    assert out == [404]
    assert len(sess.calls) == 1
    assert sess.calls[0].get("Cookie") == "ck"


def test_pixiv_info_default_probes_anonymously_first():
    sess = _FakeSession()
    pixiv_api.Pixiv_info("https://www.pixiv.net/artworks/123", Agent="UA",
                         cookie="ck", session=sess)
    assert "Cookie" not in sess.calls[0]  # 第一發匿名（既有行為不變）


# ── _run_query_accountless 停止安全 ─────────────────────────────────────────
def test_run_query_accountless_returns_none_on_stop():
    t = _thread()
    t._stop_event.set()
    assert t._run_query_accountless("123", drop_overall_inline=True) is None


# ── _process_one_pid_core 整合 ──────────────────────────────────────────────
def _stub_download_side(t):
    t._seed_pending_urls = lambda pid, u: None
    t._mark_urls_done = lambda u: None
    t._persist_pid_meta = lambda pid: None
    t.downloader._maybe_flush_exist_pid = lambda pid: None
    t.downloader._download_pid_group = lambda pid, u: []


def test_settled_pid_never_acquires_account(monkeypatch):
    t = _thread()
    _enable_probe(t)
    monkeypatch.setattr(t, "_probe_pid_anonymously", lambda pid: "stashed")
    monkeypatch.setattr(t, "_run_query_accountless",
                        lambda pid, drop: ["https://x/123_p0.jpg"])
    monkeypatch.setattr(t, "_drop_already_downloaded", lambda pid, urls: [])
    flushed = []
    t.downloader._maybe_flush_exist_pid = lambda pid: flushed.append(pid)
    t._persist_pid_meta = lambda pid: flushed.append(("meta", pid))
    t._acquire_account = lambda *a, **k: pytest.fail("settled PID must not acquire")

    failed = t._process_one_pid("123", needs_query=True)

    assert failed == []
    assert t._last_pid_ok is True
    assert flushed == ["123", ("meta", "123")]  # 全在磁碟 → 收尾記帳有做


def test_filtered_pid_settles_without_account(monkeypatch):
    t = _thread()
    _enable_probe(t)
    monkeypatch.setattr(t, "_probe_pid_anonymously", lambda pid: "stashed")
    monkeypatch.setattr(t, "_run_query_accountless", lambda pid, drop: [])
    flushed = []
    t.downloader._maybe_flush_exist_pid = lambda pid: flushed.append(pid)
    t._acquire_account = lambda *a, **k: pytest.fail("filtered PID must not acquire")

    failed = t._process_one_pid("123", needs_query=True)

    assert failed == []
    assert t._last_pid_ok is True
    assert flushed == []  # 被過濾 ≠ 已完成，不寫 exist_pid


def test_prefetched_download_releases_work_units_zero(monkeypatch):
    t = _thread()
    _enable_probe(t)
    urls = ["https://x/123_p0.jpg", "https://x/123_p1.jpg"]
    monkeypatch.setattr(t, "_probe_pid_anonymously", lambda pid: "stashed")
    monkeypatch.setattr(t, "_run_query_accountless", lambda pid, drop: list(urls))
    monkeypatch.setattr(t, "_drop_already_downloaded", lambda pid, u: list(u))
    _stub_download_side(t)
    released = {}
    t._acquire_account = lambda *a, **k: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: released.update(
        ok=ok, work_units=work_units, pages=pages)
    waits = []
    monkeypatch.setattr(t, "_wait_interruptible", lambda s: waits.append(s) or True)

    def fake_retry(label, fn):
        assert "下載" in label, "探測已代付查詢，不該再走查詢 retry"
        return True, fn(), None
    t._run_with_network_retry = fake_retry

    failed = t._process_one_pid("123", needs_query=True)

    assert failed == []
    assert t._last_pid_ok is True
    assert released == {"ok": True, "work_units": 0, "pages": 2}
    assert waits == []  # 帳號第一個請求就是下載，1-3 秒緩衝不再需要


def test_needs_cookie_falls_back_to_account_query(monkeypatch):
    t = _thread()
    _enable_probe(t)
    monkeypatch.setattr(t, "_probe_pid_anonymously", lambda pid: "needs_cookie")
    _stub_download_side(t)
    released = {}
    t._acquire_account = lambda *a, **k: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: released.update(
        work_units=work_units, pages=pages)
    labels = []

    def fake_retry(label, fn):
        labels.append(label)
        if "下載" in label:
            return True, fn(), None
        return True, ["https://x/123_p0.jpg"], None
    t._run_with_network_retry = fake_retry

    failed = t._process_one_pid("123", needs_query=True)

    assert failed == []
    assert any("下載" not in l for l in labels)  # 帳號查詢有跑
    assert released == {"work_units": 1, "pages": 1}


# ── scheduler：work_units=0 只計分頁冷卻 ────────────────────────────────────
def test_release_work_units_zero_charges_pages_only():
    a = AccountState("c1", "A")
    pause, stop = threading.Event(), threading.Event()
    pause.set()
    sched = AccountScheduler([a], lambda: 100, pause, stop)
    sched.release(a, ok=True, work_units=0, pages=2)
    rest = a.cooldown_until - time.monotonic()
    assert 8 <= rest <= 12  # 2 頁 × 5 秒，不含整份 avg=100


def test_release_default_work_unit_unchanged():
    a = AccountState("c1", "A")
    pause, stop = threading.Event(), threading.Event()
    pause.set()
    sched = AccountScheduler([a], lambda: 100, pause, stop)
    sched.release(a, ok=True, pages=0)
    rest = a.cooldown_until - time.monotonic()
    assert 98 <= rest <= 102  # 既有行為：1 × avg
