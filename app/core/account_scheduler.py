from __future__ import annotations
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.core.worker_event import WorkerEvent


@dataclass
class AccountState:
    cookie: str
    alias: str
    proxy_url: str | None = None
    cooldown_until: float = 0.0          # time.monotonic() timestamp
    disabled_reason: str | None = None   # None = active; 'proxy_dead' = disabled
    held: bool = False                   # checked out by a worker, not yet released

    @property
    def proxies(self) -> dict | None:
        from app.core.proxy_utils import to_requests_proxies
        return to_requests_proxies(self.proxy_url)


_THROUGHPUT_JITTER = 0.10  # ±10% on inter-request gap (anti-detection)


class AccountScheduler:
    """Round-robin per-account cooldown scheduler with a throughput gate.

    Per-account cooldown is the deterministic setting value itself
    (``pid_cooldown_avg`` seconds, fixed regardless of account count —
    the user dials "each account rests N seconds"), so account rest
    time stays predictable. Throughput is therefore ``avg / N``.
    Randomness lives on the throughput gate: each successful acquire
    pushes ``next_emit_at`` forward by ``throughput × random(0.9, 1.1)``,
    bounding the inter-request gap to ~±10% around throughput.

    Initial per-account cooldowns are staggered by one throughput
    interval each, so the first round of acquires fires at throughput
    cadence and the UI countdown reflects "when will the next request
    fire" rather than the longer per-account wait.

    Multi-consumer safe: acquire() marks the account ``held`` so a
    concurrent worker can never check out the same account before it is
    released; release() (or release_neutral()) clears the flag.
    Thread-safe via internal lock.
    """

    def __init__(
        self,
        accounts: list[AccountState],
        get_cooldown_avg: Callable[[], float],
        pause_event: threading.Event,
        stop_event: threading.Event,
        emit: Callable[[str], None] | None = None,
        q: Any = None,
        on_disable: Callable[[AccountState], None] | None = None,
        on_success: Callable[[AccountState], None] | None = None,
    ) -> None:
        self._accounts = list(accounts)
        self._get_cooldown_avg = get_cooldown_avg
        self._pause_event = pause_event
        self._stop_event = stop_event
        self._emit = emit if emit is not None else (lambda _: None)
        self._q = q  # queue.Queue for WorkerEvent countdown ticks
        # Called after a runtime failure marks an account disabled, so the
        # caller can persist the cookie's status (e.g., "失效") to settings
        # — the next run won't trust the cached "有效" any more.
        self._on_disable = on_disable
        # Called after EVERY successful release so the caller can refresh the
        # cookie's last_tested_at. Firing on every success (not just the first)
        # keeps the cookies view's 「檢查：」 time advancing throughout a run
        # instead of freezing at each cookie's first-use time, and continuously
        # extends the 30-day trust window.
        self._on_success = on_success
        self._lock = threading.Lock()
        self._next_emit_at = 0.0  # global throughput gate
        self._empty_pool_emitted = False
        self._last_countdown_emit = 0.0  # monotonic ts of last non-zero countdown tick
        self._stagger_initial_cooldowns()

    def _stagger_initial_cooldowns(self) -> None:
        """Spread initial per-account cooldown_until by one throughput
        interval each, so the first round of acquires fires at the
        throughput rate instead of all on top of each other."""
        active = [a for a in self._accounts if a.disabled_reason is None]
        n = len(active)
        if n <= 1:
            return
        try:
            avg = float(self._get_cooldown_avg())
        except Exception:
            return
        if avg <= 0:
            return
        throughput = avg / n
        now = time.monotonic()
        for i, acc in enumerate(active):
            acc.cooldown_until = now + i * throughput

    # ── public API ─────────────────────────────────────────────────────────

    def _wait_for_resume(self) -> bool:
        """Block on pause; returns False when stop_event fires during the wait."""
        while not self._pause_event.is_set():
            if self._stop_event.is_set():
                return False
            self._pause_event.wait(timeout=0.5)
        return not self._stop_event.is_set()

    def _empty_active_pool_response(self) -> None:
        """When no active accounts remain, log a final message once."""
        if self._accounts and not self._empty_pool_emitted:
            self._empty_pool_emitted = True
            self._emit(
                "<p><font color='red'>所有 Cookie 都已禁用，任務停止</font></p>"
            )
        self._emit_countdown(0)

    def _try_pickup_ready_account(self, active, now):
        """Return an account ready to pick up (and advance the throughput gate),
        or None if every active account is still in cooldown or held."""
        available = [a for a in active if not a.held and a.cooldown_until <= now]
        if not available:
            return None
        throughput = self._throughput_seconds(active)
        jitter = 1.0 + random.uniform(-_THROUGHPUT_JITTER, _THROUGHPUT_JITTER)
        self._next_emit_at = now + max(0.5, throughput * jitter)
        picked = available[0]
        picked.held = True
        self._emit_countdown(0)
        return picked

    def _compute_wait(self, active, now) -> float:
        """How long to sleep before the next acquire attempt; 0 means ready now."""
        free = [a for a in active if not a.held]
        if not free:
            # Every active account is checked out by another worker; poll
            # until one of them is released.
            return 0.5
        earliest_acc = min(a.cooldown_until for a in free)
        acc_wait = max(0.0, earliest_acc - now)
        gate_wait = max(0.0, self._next_emit_at - now)
        return max(acc_wait, gate_wait)

    def acquire(self) -> AccountState | None:
        """Block until an account is ready and the throughput gate has
        elapsed. Returns None when stop fires or all accounts are disabled."""
        while not self._stop_event.is_set():
            if not self._wait_for_resume():
                self._emit_countdown(0)
                return None

            with self._lock:
                active = [a for a in self._accounts if a.disabled_reason is None]
                if not active:
                    self._empty_active_pool_response()
                    return None
                now = time.monotonic()
                wait = self._compute_wait(active, now)
                if wait <= 0:
                    picked = self._try_pickup_ready_account(active, now)
                    if picked is not None:
                        return picked

            # Emit countdown tick for UI display
            self._emit_countdown(int(wait) + 1 if wait > 0 else 0)
            # Poll at most 0.5 s so stop/pause can interrupt
            time.sleep(max(0.001, min(0.5, wait)))

        self._emit_countdown(0)
        return None

    def _throughput_seconds(self, active: list[AccountState]) -> float:
        n = max(1, len(active))
        return self._get_cooldown_avg() / n

    def _emit_countdown(self, remaining: int) -> None:
        """Push a countdown event to the queue if available.

        Non-zero ticks are throttled to one per second across all workers
        (multiple pool workers poll acquire() concurrently — unthrottled
        each would emit its own remaining-seconds value and the UI countdown
        would jump between them)."""
        if self._q is None:
            return
        if remaining > 0:
            now = time.monotonic()
            with self._lock:
                if now - self._last_countdown_emit < 1.0:
                    return
                self._last_countdown_emit = now
        try:
            self._q.put(WorkerEvent("countdown", remaining))
        except Exception:
            pass

    def _mark_failure_locked(self, account: AccountState) -> Callable[[AccountState], None] | None:
        """Disable + emit; caller must hold ``self._lock``. Returns the
        on_disable callback to invoke outside the lock (or None)."""
        account.disabled_reason = "proxy_dead"
        alias = account.alias or account.cookie[:20]
        proxy = account.proxy_url or "本機IP"
        self._emit(
            f"<p><font color='red'>Cookie「{alias}」proxy「{proxy}」"
            f"連不通，本輪禁用</font></p>"
        )
        return self._on_disable

    def _mark_success_locked(self, account: AccountState) -> Callable[[AccountState], None] | None:
        """Schedule per-account cooldown; caller must hold ``self._lock``.
        Returns the on_success callback (fired on EVERY successful release) so
        the caller refreshes the cookie's last_tested_at each time — the cookies
        view 「檢查：」 time then advances live during a run instead of freezing
        at first use."""
        per_account = max(1.0, self._get_cooldown_avg())
        account.cooldown_until = time.monotonic() + per_account
        return self._on_success

    @staticmethod
    def _safe_call(cb: Callable[[AccountState], None] | None, account: AccountState) -> None:
        if cb is None:
            return
        try:
            cb(account)
        except Exception:
            pass

    def release(self, account: AccountState, ok: bool = True) -> None:
        """Record outcome after a unit of work completes.

        ok=True  -> schedule per-account cooldown to exactly the setting
                    value (``pid_cooldown_avg`` seconds; no jitter on
                    per-account; randomness is applied at the throughput
                    gate in acquire() so inter-request gap stays bounded).
        ok=False -> disable account (proxy unreachable).
        """
        with self._lock:
            account.held = False
            cb = self._mark_failure_locked(account) if not ok else self._mark_success_locked(account)
        # Callback runs outside the lock — it does file I/O and we don't
        # want to block other acquire/release threads.
        self._safe_call(cb, account)

    def release_neutral(self, account: AccountState) -> None:
        """Return the account without judging the work's outcome.

        Used when the unit of work was aborted (user stop) or failed for a
        reason unrelated to the account (disk error, decode error): the
        account is neither disabled nor credited with a success — but it
        still gets the normal per-account cooldown so an abort can't be
        used to hammer the same account immediately.
        """
        with self._lock:
            account.held = False
            account.cooldown_until = time.monotonic() + max(1.0, self._get_cooldown_avg())

    def disable(self, account: AccountState, reason: str) -> None:
        with self._lock:
            account.disabled_reason = reason
            account.held = False

    def _all_disabled(self) -> bool:
        """Internal: caller must hold ``self._lock``."""
        return bool(self._accounts) and all(
            a.disabled_reason is not None for a in self._accounts
        )

    def all_disabled(self) -> bool:
        with self._lock:
            return self._all_disabled()

    def average_cooldown(self) -> float:
        """Average inter-request throughput in seconds — i.e., the
        expected gap until the next request fires across all accounts.
        Equals ``avg / N`` where N is the active account count."""
        n = max(1, len([a for a in self._accounts if a.disabled_reason is None]))
        return self._get_cooldown_avg() / n
