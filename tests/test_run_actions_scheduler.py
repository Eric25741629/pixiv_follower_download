"""Tests for RunController cookie extraction, validation, and
AccountScheduler construction from settings."""
import threading
import time
from queue import Queue
import pytest
from app.gui.run_actions import RunController
from app.core import pixiv_api


@pytest.fixture
def run_controller():
    main_view = object()  # Not used by these methods
    event_q = Queue()
    return RunController(main_view, event_q)


def _events_pair():
    pause = threading.Event()
    pause.set()
    stop = threading.Event()
    return pause, stop


def _build_from_auth(rc, auth):
    entries = rc._extract_cookie_entries(auth)
    cookies = [e["cookie"] for e in entries if e.get("cookie", "").strip()]
    pause, stop = _events_pair()
    return rc._build_scheduler(auth, cookies, pause, stop)


def test_build_scheduler_with_cookies_entries(run_controller):
    auth = {
        "cookies_entries": [
            {"cookie": "c1", "alias": "Main"},
            {"cookie": "c2", "alias": "Backup"},
        ],
        "cookies_aliases": {"c1": "Main", "c2": "Backup"},
        "cookie_proxy_map": {"c1": "http://1.2.3.4:8080"},
    }
    sched = _build_from_auth(run_controller, auth)
    accounts = sched._accounts
    assert len(accounts) == 2
    assert accounts[0].cookie == "c1"
    assert accounts[0].alias == "Main"
    assert accounts[0].proxy_url == "http://1.2.3.4:8080"
    assert accounts[1].cookie == "c2"
    assert accounts[1].proxy_url is None  # no map entry


def test_build_scheduler_falls_back_to_cookies_pool(run_controller):
    auth = {
        "cookies_pool": ["c1", "c2"],
        "cookies_aliases": {"c1": "A1"},
        "cookie_proxy_map": {},
    }
    sched = _build_from_auth(run_controller, auth)
    accounts = sched._accounts
    assert len(accounts) == 2
    assert accounts[0].cookie == "c1"
    assert accounts[0].alias == "A1"
    assert accounts[1].cookie == "c2"
    assert accounts[1].alias == "Cookie 2"


def test_build_scheduler_falls_back_to_single_cookie(run_controller):
    auth = {
        "cookies": "single_cookie_value",
        "cookies_pool": [],
        "cookies_aliases": {},
        "cookie_proxy_map": {},
    }
    sched = _build_from_auth(run_controller, auth)
    accounts = sched._accounts
    assert len(accounts) == 1
    assert accounts[0].cookie == "single_cookie_value"


def test_build_scheduler_empty_settings_yields_empty(run_controller):
    auth = {"cookies": "", "cookies_pool": [], "cookies_entries": []}
    sched = _build_from_auth(run_controller, auth)
    assert sched._accounts == []


def test_build_scheduler_skips_blank_cookies(run_controller):
    auth = {
        "cookies_entries": [
            {"cookie": "c1"},
            {"cookie": ""},
            {"cookie": "   "},
            {"cookie": "c2"},
        ],
        "cookies_aliases": {},
        "cookie_proxy_map": {},
    }
    sched = _build_from_auth(run_controller, auth)
    cookies = [a.cookie for a in sched._accounts]
    assert cookies == ["c1", "c2"]


def test_build_scheduler_invalid_proxy_url_falls_back_to_none(run_controller):
    auth = {
        "cookies_entries": [{"cookie": "c1"}],
        "cookies_aliases": {},
        "cookie_proxy_map": {"c1": "not-a-url-at-all"},
    }
    sched = _build_from_auth(run_controller, auth)
    assert sched._accounts[0].proxy_url is None


# ── _test_cookies ──────────────────────────────────────────────────────────


def _entry(cookie, status=None, last_tested_at=None, alias=""):
    e = {"cookie": cookie, "alias": alias}
    if status is not None:
        e["status"] = status
    if last_tested_at is not None:
        e["last_tested_at"] = last_tested_at
    return e


def test_test_cookies_filters_invalid(run_controller, monkeypatch):
    # Test_cookies returns (count_valid, list_of_valid). Stub it so c1 and
    # c3 pass and c2 fails — _test_cookies should keep only c1 and c3.
    def fake_test(lst, agent):
        c = lst[0]
        return (1, [c]) if c in {"c1", "c3"} else (0, [])

    monkeypatch.setattr(pixiv_api, "Test_cookies", fake_test)
    monkeypatch.setattr(run_controller, "_persist_cookie_statuses", lambda *a, **k: None)

    entries = [_entry("c1"), _entry("c2"), _entry("c3")]
    valid = run_controller._test_cookies(entries, "agent")
    assert valid == ["c1", "c3"]


def test_test_cookies_returns_empty_for_empty_input(run_controller):
    assert run_controller._test_cookies([], "agent") == []


def test_test_cookies_treats_exception_as_invalid(run_controller, monkeypatch):
    def fake_test(lst, agent):
        raise RuntimeError("network down")

    monkeypatch.setattr(pixiv_api, "Test_cookies", fake_test)
    monkeypatch.setattr(run_controller, "_persist_cookie_statuses", lambda *a, **k: None)

    assert run_controller._test_cookies([_entry("c1"), _entry("c2")], "agent") == []


def test_test_cookies_skips_recent_valid_cache(run_controller, monkeypatch):
    # c1 has cached 有效 within 30 days → no Test_cookies call.
    # c2 is stale (35 days) → should hit network.
    # c3 has cached 失效 → should hit network.
    calls: list[str] = []

    def fake_test(lst, agent):
        calls.append(lst[0])
        return (1, [lst[0]])  # everything tested passes

    monkeypatch.setattr(pixiv_api, "Test_cookies", fake_test)
    monkeypatch.setattr(run_controller, "_persist_cookie_statuses", lambda *a, **k: None)

    now = time.time()
    entries = [
        _entry("c1", status="有效", last_tested_at=now - 5 * 86400),    # 5 days
        _entry("c2", status="有效", last_tested_at=now - 35 * 86400),   # stale
        _entry("c3", status="失效", last_tested_at=now - 1 * 86400),    # cached bad
    ]
    valid = run_controller._test_cookies(entries, "agent")
    assert valid == ["c1", "c2", "c3"]
    assert sorted(calls) == ["c2", "c3"]


def test_test_cookies_tests_when_no_timestamp(run_controller, monkeypatch):
    # status=有效 but no last_tested_at → must re-test.
    calls: list[str] = []

    def fake_test(lst, agent):
        calls.append(lst[0])
        return (1, [lst[0]])

    monkeypatch.setattr(pixiv_api, "Test_cookies", fake_test)
    monkeypatch.setattr(run_controller, "_persist_cookie_statuses", lambda *a, **k: None)

    valid = run_controller._test_cookies([_entry("c1", status="有效")], "agent")
    assert valid == ["c1"]
    assert calls == ["c1"]


def test_invalidate_cookie_status_writes_失效(run_controller, monkeypatch):
    # _invalidate_cookie_status should rewrite settings via SettingsStore.
    captured = {}

    class FakeStore:
        def get_section(self, name):
            return {
                "cookies_entries": [
                    {"cookie": "c1", "alias": "A1", "status": "有效", "last_tested_at": 1.0},
                    {"cookie": "c2", "alias": "A2"},
                ],
            }

        def update_section(self, name, value):
            captured["value"] = value

    monkeypatch.setattr("app.gui.run_actions._store", lambda: FakeStore())

    run_controller._invalidate_cookie_status("c1")
    new_entries = captured["value"]["cookies_entries"]
    assert new_entries[0]["status"] == "失效"
    assert new_entries[0]["last_tested_at"] > 1.0  # bumped to now
    assert new_entries[1] == {"cookie": "c2", "alias": "A2"}  # untouched

    # A live cookie_status event must be emitted so the cookies view flips to
    # 失效 immediately, not only after the next reload_from_settings.
    events = []
    while not run_controller._event_q.empty():
        events.append(run_controller._event_q.get_nowait())
    cookie_status = [e for e in events if getattr(e, "type", None) == "cookie_status"]
    assert cookie_status, "on_disable must emit a cookie_status event"
    payload = cookie_status[-1].data
    assert payload[0] == "c1"
    assert payload[1] == "失效"


def test_attach_aliases_pairs_cookies_with_alias_map(run_controller):
    auth = {
        "cookies_aliases": {"c1": "Main", "c2": "Backup"},
    }
    paired = run_controller._attach_aliases(["c1", "c2", "c3"], auth)
    assert paired == [
        {"cookie": "c1", "alias": "Main"},
        {"cookie": "c2", "alias": "Backup"},
        {"cookie": "c3", "alias": ""},
    ]


def test_attach_aliases_handles_missing_or_invalid_map(run_controller):
    # No alias map at all.
    assert run_controller._attach_aliases(["c1"], {}) == [
        {"cookie": "c1", "alias": ""}
    ]
    # Non-dict alias map (corrupt settings).
    assert run_controller._attach_aliases(
        ["c1"], {"cookies_aliases": "not-a-dict"},
    ) == [{"cookie": "c1", "alias": ""}]


def test_attach_aliases_flow_into_thread_alias_map():
    """End-to-end: aliases attached by _attach_aliases must surface in the
    worker thread's _cookie_alias_map so cookie_usage_label resolves to
    the alias instead of falling back to ``Cookie{n}``."""
    from app.core.pixiv_thread_utils import init_cookie_fields, cookie_usage_label

    paired = [
        {"cookie": "c1", "alias": "Main"},
        {"cookie": "c2", "alias": "Backup"},
    ]
    _entries, pool, alias_map, _first = init_cookie_fields(paired)
    assert alias_map == {"c1": "Main", "c2": "Backup"}
    assert cookie_usage_label("c1", pool, alias_map) == "Main"
    assert cookie_usage_label("c2", pool, alias_map) == "Backup"
