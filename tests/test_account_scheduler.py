import time
import threading
import pytest
from app.core.account_scheduler import AccountState, AccountScheduler


def _make_scheduler(accounts, avg=60.0):
    pause = threading.Event()
    pause.set()
    stop = threading.Event()
    return AccountScheduler(
        accounts=accounts,
        get_cooldown_avg=lambda: avg,
        pause_event=pause,
        stop_event=stop,
    ), pause, stop


def test_acquire_returns_first_available():
    acc = AccountState(cookie="c1", alias="A1")
    sched, _, _ = _make_scheduler([acc])
    result = sched.acquire()
    assert result is acc


def test_acquire_skips_disabled():
    acc1 = AccountState(cookie="c1", alias="A1")
    acc2 = AccountState(cookie="c2", alias="A2")
    acc1.disabled_reason = "proxy_dead"
    sched, _, _ = _make_scheduler([acc1, acc2])
    result = sched.acquire()
    assert result is acc2


def test_release_sets_cooldown_above_zero():
    acc = AccountState(cookie="c1", alias="A1")
    sched, _, _ = _make_scheduler([acc], avg=60.0)
    sched.release(acc, ok=True)
    assert acc.cooldown_until > time.monotonic()


def test_release_cooldown_within_jitter_range():
    acc = AccountState(cookie="c1", alias="A1")
    before = time.monotonic()
    sched, _, _ = _make_scheduler([acc], avg=10.0)
    sched.release(acc, ok=True)
    low = before + max(1, int(10 * 0.7))
    high = before + int(10 * 1.3) + 1
    assert low <= acc.cooldown_until <= high


def test_release_ok_false_disables_account():
    acc = AccountState(cookie="c1", alias="A1")
    sched, _, _ = _make_scheduler([acc])
    sched.release(acc, ok=False)
    assert acc.disabled_reason == "proxy_dead"


def test_all_disabled_true_when_all_disabled():
    acc1 = AccountState(cookie="c1", alias="A1", disabled_reason="proxy_dead")
    acc2 = AccountState(cookie="c2", alias="A2", disabled_reason="proxy_dead")
    sched, _, _ = _make_scheduler([acc1, acc2])
    assert sched.all_disabled() is True


def test_all_disabled_false_when_one_active():
    acc1 = AccountState(cookie="c1", alias="A1", disabled_reason="proxy_dead")
    acc2 = AccountState(cookie="c2", alias="A2")
    sched, _, _ = _make_scheduler([acc1, acc2])
    assert sched.all_disabled() is False


def test_acquire_returns_none_on_stop():
    acc = AccountState(cookie="c1", alias="A1")
    acc.cooldown_until = time.monotonic() + 9999.0
    sched, _, stop = _make_scheduler([acc])
    stop.set()
    result = sched.acquire()
    assert result is None


def test_acquire_waits_then_returns_after_cooldown(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda _: None)

    acc = AccountState(cookie="c1", alias="A1")
    acc.cooldown_until = 5.0
    sched, _, _ = _make_scheduler([acc], avg=10.0)

    clock[0] = 6.0
    result = sched.acquire()
    assert result is acc


def test_account_state_proxies_property_with_proxy():
    acc = AccountState(cookie="c1", alias="A1", proxy_url="http://1.2.3.4:8080")
    assert acc.proxies == {"http": "http://1.2.3.4:8080", "https": "http://1.2.3.4:8080"}


def test_account_state_proxies_property_without_proxy():
    acc = AccountState(cookie="c1", alias="A1", proxy_url=None)
    assert acc.proxies is None
