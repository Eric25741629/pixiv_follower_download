from __future__ import annotations
import math
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

    @property
    def proxies(self) -> dict | None:
        from app.core.proxy_utils import to_requests_proxies
        return to_requests_proxies(self.proxy_url)


_THROUGHPUT_JITTER = 0.10  # ±10% on inter-request gap (anti-detection)


class AccountScheduler:
    """Round-robin per-account cooldown scheduler with a throughput gate.

    Per-account cooldown is the deterministic ``avg × ln(N+1)`` (no
    jitter on it), so account rest time stays predictable. Randomness
    lives on the throughput gate: each successful acquire pushes
    ``next_emit_at`` forward by ``throughput × random(0.9, 1.1)``,
    bounding the inter-request gap to ~±10% around throughput.

    Initial per-account cooldowns are staggered by one throughput
    interval each, so the first round of acquires fires at throughput
    cadence and the UI countdown reflects "when will the next request
    fire" rather than the longer per-account wait.

    Single-consumer: one worker thread calls acquire(), runs a unit of
    work, and calls release(). Thread-safe via internal lock.
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
        self._lock = threading.Lock()
        self._next_emit_at = 0.0  # global throughput gate
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
        throughput = avg * math.log(n + 1) / n
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
        """When no active accounts remain, log a final message if any existed."""
        if self._accounts:
            self._emit(
                "<p><font color='red'>所有 Cookie 都已禁用，任務停止</font></p>"
            )
        self._emit_countdown(0)

    def _try_pickup_ready_account(self, active, now):
        """Return an account ready to pick up (and advance the throughput gate),
        or None if every active account is still in cooldown."""
        available = [a for a in active if a.cooldown_until <= now]
        if not available:
            return None
        throughput = self._throughput_seconds(active)
        jitter = 1.0 + random.uniform(-_THROUGHPUT_JITTER, _THROUGHPUT_JITTER)
        self._next_emit_at = now + max(0.5, throughput * jitter)
        self._emit_countdown(0)
        return available[0]

    def _compute_wait(self, active, now) -> float:
        """How long to sleep before the next acquire attempt; 0 means ready now."""
        earliest_acc = min(a.cooldown_until for a in active)
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
        return self._get_cooldown_avg() * math.log(n + 1) / n

    def _emit_countdown(self, remaining: int) -> None:
        """Push a countdown event to the queue if available."""
        if self._q is not None:
            try:
                self._q.put(WorkerEvent("countdown", remaining))
            except Exception:
                pass

    def release(self, account: AccountState, ok: bool = True) -> None:
        """Record outcome after a unit of work completes.

        ok=True  -> schedule per-account cooldown to exactly
                    ``avg × ln(N+1)`` seconds (no jitter on per-account;
                    randomness is applied at the throughput gate in
                    acquire() so inter-request gap stays bounded).
        ok=False -> disable account (proxy unreachable).
        """
        with self._lock:
            if not ok:
                account.disabled_reason = "proxy_dead"
                alias = account.alias or account.cookie[:20]
                proxy = account.proxy_url or "本機IP"
                self._emit(
                    f"<p><font color='red'>Cookie「{alias}」proxy「{proxy}」"
                    f"連不通，本輪禁用</font></p>"
                )
                disable_cb = self._on_disable
                disabled_account = account
            else:
                avg = self._get_cooldown_avg()
                n = max(1, len([a for a in self._accounts if a.disabled_reason is None]))
                per_account = max(1.0, avg * math.log(n + 1))
                account.cooldown_until = time.monotonic() + per_account
                disable_cb = None
                disabled_account = None
        # Call on_disable callback outside the lock — it does file I/O
        # and we don't want to block other acquire/release threads.
        if disable_cb is not None and disabled_account is not None:
            try:
                disable_cb(disabled_account)
            except Exception:
                pass

    def disable(self, account: AccountState, reason: str) -> None:
        with self._lock:
            account.disabled_reason = reason

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
        Equals ``avg × ln(N+1) / N`` where N is the active account count."""
        n = max(1, len([a for a in self._accounts if a.disabled_reason is None]))
        return self._get_cooldown_avg() * math.log(n + 1) / n
