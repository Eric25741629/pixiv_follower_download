"""Tests for RunController._build_scheduler -- AccountScheduler construction
from settings.
"""
import threading
from queue import Queue
import pytest
from app.gui.run_actions import RunController


@pytest.fixture
def run_controller():
    main_view = object()  # Not used by _build_scheduler
    event_q = Queue()
    return RunController(main_view, event_q)


def _events_pair():
    pause = threading.Event()
    pause.set()
    stop = threading.Event()
    return pause, stop


def test_build_scheduler_with_cookies_entries(run_controller):
    auth = {
        "cookies_entries": [
            {"cookie": "c1", "alias": "Main"},
            {"cookie": "c2", "alias": "Backup"},
        ],
        "cookies_aliases": {"c1": "Main", "c2": "Backup"},
        "cookie_proxy_map": {"c1": "http://1.2.3.4:8080"},
    }
    perf = {"pid_cooldown_avg": 35}
    pause, stop = _events_pair()
    sched = run_controller._build_scheduler(auth, perf, pause, stop)
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
    perf = {"pid_cooldown_avg": 35}
    pause, stop = _events_pair()
    sched = run_controller._build_scheduler(auth, perf, pause, stop)
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
    perf = {"pid_cooldown_avg": 35}
    pause, stop = _events_pair()
    sched = run_controller._build_scheduler(auth, perf, pause, stop)
    accounts = sched._accounts
    assert len(accounts) == 1
    assert accounts[0].cookie == "single_cookie_value"


def test_build_scheduler_empty_settings_yields_empty(run_controller):
    auth = {"cookies": "", "cookies_pool": [], "cookies_entries": []}
    perf = {"pid_cooldown_avg": 35}
    pause, stop = _events_pair()
    sched = run_controller._build_scheduler(auth, perf, pause, stop)
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
    perf = {"pid_cooldown_avg": 35}
    pause, stop = _events_pair()
    sched = run_controller._build_scheduler(auth, perf, pause, stop)
    cookies = [a.cookie for a in sched._accounts]
    assert cookies == ["c1", "c2"]


def test_build_scheduler_invalid_proxy_url_falls_back_to_none(run_controller):
    auth = {
        "cookies_entries": [{"cookie": "c1"}],
        "cookies_aliases": {},
        "cookie_proxy_map": {"c1": "not-a-url-at-all"},
    }
    perf = {"pid_cooldown_avg": 35}
    pause, stop = _events_pair()
    sched = run_controller._build_scheduler(auth, perf, pause, stop)
    assert sched._accounts[0].proxy_url is None
