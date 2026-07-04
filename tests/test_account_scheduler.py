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


def test_release_cooldown_is_deterministic():
    # Per-account cooldown is exactly the setting value (no jitter on this).
    # Randomness lives on the throughput gate in acquire(), not here.
    acc = AccountState(cookie="c1", alias="A1")
    before = time.monotonic()
    sched, _, _ = _make_scheduler([acc], avg=10.0)
    sched.release(acc, ok=True)
    expected = 10.0
    assert before + expected - 0.05 <= acc.cooldown_until <= before + expected + 0.05


def test_release_pages_add_flat_surcharge_not_full_avg():
    # Cooldown = avg × work_units + PAGE_COOLDOWN_SEC × pages: a 4-page PID
    # rests avg + 4×5s, NOT avg×5 (pages are cheap; only the query is full-cost).
    from app.core.account_scheduler import PAGE_COOLDOWN_SEC
    acc = AccountState(cookie="c1", alias="A1")
    before = time.monotonic()
    sched, _, _ = _make_scheduler([acc], avg=45.0)
    sched.release(acc, ok=True, pages=4)
    expected = 45.0 + 4 * PAGE_COOLDOWN_SEC
    assert before + expected - 0.05 <= acc.cooldown_until <= before + expected + 0.05


def test_release_ok_false_disables_account():
    acc = AccountState(cookie="c1", alias="A1")
    sched, _, _ = _make_scheduler([acc])
    sched.release(acc, ok=False)
    assert acc.disabled_reason == "proxy_dead"


def test_release_ok_false_fires_on_disable_callback():
    acc = AccountState(cookie="c1", alias="A1")
    pause = threading.Event()
    pause.set()
    stop = threading.Event()
    captured: list = []
    sched = AccountScheduler(
        accounts=[acc],
        get_cooldown_avg=lambda: 30.0,
        pause_event=pause,
        stop_event=stop,
        on_disable=lambda a: captured.append(a.cookie),
    )
    sched.release(acc, ok=False)
    assert captured == ["c1"]


def test_release_ok_true_does_not_fire_on_disable():
    acc = AccountState(cookie="c1", alias="A1")
    pause = threading.Event()
    pause.set()
    stop = threading.Event()
    captured: list = []
    sched = AccountScheduler(
        accounts=[acc],
        get_cooldown_avg=lambda: 30.0,
        pause_event=pause,
        stop_event=stop,
        on_disable=lambda a: captured.append(a.cookie),
    )
    sched.release(acc, ok=True)
    assert captured == []


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


def test_acquire_returns_none_when_accounts_empty():
    sched, _, _ = _make_scheduler([])
    assert sched.acquire() is None


def test_all_disabled_false_when_accounts_empty():
    sched, _, _ = _make_scheduler([])
    assert sched.all_disabled() is False


def test_initial_cooldowns_staggered_by_throughput():
    accs = [AccountState(cookie=f"c{i}", alias=f"A{i}") for i in range(3)]
    before = time.monotonic()
    sched, _, _ = _make_scheduler(accs, avg=30.0)
    throughput = 30.0 / 3  # N=3
    # First account ready immediately, each subsequent staggered by throughput.
    assert accs[0].cooldown_until <= before + 0.01
    assert before + throughput - 0.5 <= accs[1].cooldown_until <= before + throughput + 0.5
    assert before + 2 * throughput - 0.5 <= accs[2].cooldown_until <= before + 2 * throughput + 0.5


def test_initial_cooldowns_no_stagger_for_single_account():
    acc = AccountState(cookie="c1", alias="A1")
    sched, _, _ = _make_scheduler([acc], avg=30.0)
    # N=1: stagger is a no-op so the account stays immediately available.
    assert acc.cooldown_until <= time.monotonic()


def test_average_cooldown_returns_throughput():
    accs = [AccountState(cookie=f"c{i}", alias=f"A{i}") for i in range(7)]
    sched, _, _ = _make_scheduler(accs, avg=30.0)
    expected = 30.0 / 7  # ≈ 4.29s
    assert abs(sched.average_cooldown() - expected) < 1e-6


def test_release_cooldown_is_fixed_regardless_of_account_count():
    accs = [AccountState(cookie=f"c{i}", alias=f"A{i}") for i in range(7)]
    sched, _, _ = _make_scheduler(accs, avg=30.0)
    before = time.monotonic()
    sched.release(accs[0], ok=True)
    expected = 30.0  # fixed per-account, independent of N
    assert before + expected - 0.05 <= accs[0].cooldown_until <= before + expected + 0.05


def test_acquire_advances_throughput_gate(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda _: None)
    acc = AccountState(cookie="c1", alias="A1")
    sched, _, _ = _make_scheduler([acc], avg=10.0)
    # First acquire fires immediately and advances next_emit_at.
    result = sched.acquire()
    assert result is acc
    throughput = 10.0  # N=1
    # ±10% throughput jitter on gate.
    assert clock[0] + throughput * 0.9 <= sched._next_emit_at <= clock[0] + throughput * 1.1


def test_throughput_gate_bounds_inter_request_gap(monkeypatch):
    # Even with 7 accounts, the gap between successive acquires stays
    # within ±10% of throughput because per-account cooldown is exact
    # and acquires are gated by next_emit_at.
    clock = [100.0]

    def fake_sleep(d):
        clock[0] += max(0.0, float(d))

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", fake_sleep)
    accs = [AccountState(cookie=f"c{i}", alias=f"A{i}") for i in range(7)]
    sched, _, _ = _make_scheduler(accs, avg=30.0)
    throughput = 30.0 / 7  # ≈ 4.29s

    times = []
    for _ in range(20):
        t = sched.acquire()
        assert t is not None
        times.append(clock[0])
        sched.release(t, ok=True)

    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    # All gaps must be ≤ 1.1 × throughput (no 20+ second outliers).
    assert max(gaps) <= throughput * 1.1 + 0.5
    # And in steady state ≥ 0.9 × throughput so the gate is enforced.
    steady = gaps[7:]
    assert min(steady) >= throughput * 0.9 - 0.5


def test_no_account_starves_under_demand_slack(monkeypatch):
    """Regression for the steep request-count skew / never-selected tail.

    Under demand slack (a unit of work takes longer than one throughput
    interval, the normal case for slow downloads) several accounts are
    ready at once. The old `available[0]` pick always took the lowest list
    index, so the front of the pool satisfied every request and the tail
    accounts were never selected. Idle-weighted selection must use the
    whole pool: every account picked at least once and the spread bounded.
    """
    import random as _r

    _r.seed(2024)
    clock = [100.0]

    def fake_sleep(d):
        clock[0] += max(0.0, float(d))

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", fake_sleep)

    accs = [AccountState(cookie=f"c{i}", alias=f"A{i}") for i in range(8)]
    sched, _, _ = _make_scheduler(accs, avg=30.0)  # throughput 30/8 = 3.75s

    counts = {a.cookie: 0 for a in accs}
    WORK = 10.0  # each unit of work > throughput -> several accounts pile up ready
    for _ in range(240):
        acc = sched.acquire()
        assert acc is not None
        counts[acc.cookie] += 1
        clock[0] += WORK            # simulate a slow download/query
        sched.release(acc, ok=True)

    # No account starves (the old code left tail accounts at 0).
    assert min(counts.values()) > 0
    # Busiest is not wildly more than quietest — a balanced spread, not a
    # 598-vs-0 cliff.
    assert max(counts.values()) <= 3 * min(counts.values())
