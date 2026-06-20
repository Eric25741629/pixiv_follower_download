"""Tests for the helpers extracted from AccountScheduler.acquire."""
from pathlib import Path
import sys
import threading
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.account_scheduler import AccountScheduler, AccountState


def _make_scheduler(accounts, q=None):
    return AccountScheduler(
        accounts=accounts,
        pause_event=threading.Event(),
        stop_event=threading.Event(),
        get_cooldown_avg=lambda: 30,
        emit=lambda msg: None,
        q=q,
        on_disable=None,
    )


def _make_account_at(cookie, cooldown_until):
    a = AccountState(cookie=cookie, alias=cookie.upper())
    a.cooldown_until = cooldown_until
    return a


# ── _compute_wait ───────────────────────────────────────────────────────────

def test_compute_wait_returns_zero_when_all_ready():
    sched = _make_scheduler([])
    sched._next_emit_at = 0.0
    now = 1000.0
    accs = [_make_account_at("c1", 999.0), _make_account_at("c2", 998.5)]
    wait = sched._compute_wait(accs, now)
    assert wait == 0


def test_compute_wait_returns_account_cooldown_when_largest():
    sched = _make_scheduler([])
    sched._next_emit_at = 0.0
    now = 100.0
    accs = [_make_account_at("c1", 110.0), _make_account_at("c2", 105.0)]
    # Earliest acc.cooldown_until is 105 → acc_wait = 5
    wait = sched._compute_wait(accs, now)
    assert wait == 5.0


def test_compute_wait_returns_throughput_gate_when_largest():
    sched = _make_scheduler([])
    sched._next_emit_at = 120.0
    now = 100.0
    accs = [_make_account_at("c1", 100.0)]  # acc ready immediately
    # gate_wait = 20 dominates
    wait = sched._compute_wait(accs, now)
    assert wait == 20.0


def test_compute_wait_takes_max_of_two():
    sched = _make_scheduler([])
    sched._next_emit_at = 110.0
    now = 100.0
    accs = [_make_account_at("c1", 105.0), _make_account_at("c2", 200.0)]
    # acc_wait = 5, gate_wait = 10 → max = 10
    wait = sched._compute_wait(accs, now)
    assert wait == 10.0


# ── _try_pickup_ready_account ───────────────────────────────────────────────

def test_pickup_returns_a_ready_account_and_marks_held():
    sched = _make_scheduler([])
    sched._next_emit_at = 0.0
    now = 100.0
    a, b = _make_account_at("c1", 100.0), _make_account_at("c2", 100.0)
    picked = sched._try_pickup_ready_account([a, b], now)
    # Selection among equally-idle ready accounts is weighted-random, so the
    # contract is "returns one of the ready accounts and marks it held" —
    # not the old fixed list-order `available[0]`.
    assert picked in (a, b)
    assert picked.held is True


def test_pick_weighted_favors_longer_idle_but_never_starves():
    """Among ready accounts the longer-idle one wins more often, yet the
    just-ready one still gets a turn (no starvation)."""
    import random as _r

    _r.seed(12345)
    sched = _make_scheduler([])
    now = 1000.0
    long_idle = _make_account_at("c1", 970.0)   # ready 30s ago
    just_ready = _make_account_at("c2", 1000.0)  # ready right now
    counts = {"c1": 0, "c2": 0}
    for _ in range(600):
        picked = sched._pick_weighted_by_idle([long_idle, just_ready], now, throughput=15.0)
        counts[picked.cookie] += 1
    assert counts["c1"] > counts["c2"]   # longer-idle favored
    assert counts["c2"] > 0              # but never starved


def test_pick_weighted_single_available_is_deterministic():
    sched = _make_scheduler([])
    now = 100.0
    only = _make_account_at("c1", 100.0)
    assert sched._pick_weighted_by_idle([only], now, throughput=15.0) is only


def test_pickup_returns_none_when_no_account_ready():
    sched = _make_scheduler([])
    sched._next_emit_at = 0.0
    now = 100.0
    accs = [_make_account_at("c1", 200.0)]  # cooldown 100s in future
    picked = sched._try_pickup_ready_account(accs, now)
    assert picked is None


def test_pickup_advances_throughput_gate():
    """A successful pickup must move _next_emit_at forward."""
    sched = _make_scheduler([])
    sched._next_emit_at = 0.0
    now = 100.0
    accs = [_make_account_at("c1", 100.0)]
    sched._try_pickup_ready_account(accs, now)
    # _next_emit_at must be at least now + 0.5 (the floor)
    assert sched._next_emit_at >= now + 0.5


def test_pickup_skips_account_in_cooldown():
    sched = _make_scheduler([])
    sched._next_emit_at = 0.0
    now = 100.0
    a, b = _make_account_at("c1", 200.0), _make_account_at("c2", 100.0)  # only b ready
    picked = sched._try_pickup_ready_account([a, b], now)
    assert picked is b


# ── _wait_for_resume ────────────────────────────────────────────────────────

def test_wait_for_resume_returns_true_when_unpaused():
    sched = _make_scheduler([])
    sched._pause_event.set()  # not paused
    assert sched._wait_for_resume() is True


def test_wait_for_resume_returns_false_when_stop_set():
    sched = _make_scheduler([])
    sched._pause_event.clear()
    sched._stop_event.set()
    assert sched._wait_for_resume() is False


def test_wait_for_resume_unblocks_when_pause_released():
    """Simulates a paused scheduler that gets resumed mid-wait."""
    sched = _make_scheduler([])
    sched._pause_event.clear()

    def _release_after_brief_sleep():
        time.sleep(0.1)
        sched._pause_event.set()

    threading.Thread(target=_release_after_brief_sleep, daemon=True).start()
    assert sched._wait_for_resume() is True
