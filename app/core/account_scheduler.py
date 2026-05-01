from __future__ import annotations
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable


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


class AccountScheduler:
    """Round-robin per-account cooldown scheduler.

    Single-consumer: one worker thread calls acquire(), runs a unit of
    work, and calls release(). Each account has its own cooldown timer
    so the same account never fires faster than the configured avg.
    Thread-safe via internal lock.
    """

    def __init__(
        self,
        accounts: list[AccountState],
        get_cooldown_avg: Callable[[], float],
        pause_event: threading.Event,
        stop_event: threading.Event,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self._accounts = list(accounts)
        self._get_cooldown_avg = get_cooldown_avg
        self._pause_event = pause_event
        self._stop_event = stop_event
        self._emit = emit if emit is not None else (lambda _: None)
        self._lock = threading.Lock()

    # ── public API ─────────────────────────────────────────────────────────

    def acquire(self) -> AccountState | None:
        """Block until an account is ready. Returns None when stop fires
        or when all accounts are disabled."""
        while not self._stop_event.is_set():
            # Honour pause
            while not self._pause_event.is_set():
                if self._stop_event.is_set():
                    return None
                self._pause_event.wait(timeout=0.5)
            if self._stop_event.is_set():
                return None

            with self._lock:
                if self.all_disabled():
                    self._emit(
                        "<p><font color='red'>所有 Cookie 都已禁用，任務停止</font></p>"
                    )
                    return None
                now = time.monotonic()
                available = [
                    a for a in self._accounts
                    if a.disabled_reason is None and a.cooldown_until <= now
                ]
                if available:
                    return available[0]
                earliest = min(
                    a.cooldown_until
                    for a in self._accounts
                    if a.disabled_reason is None
                )
                wait = max(0.0, earliest - now)

            # Poll at most 0.5 s so stop/pause can interrupt
            time.sleep(min(0.5, wait))

        return None

    def release(self, account: AccountState, ok: bool = True) -> None:
        """Record outcome after a unit of work completes.

        ok=True  -> schedule cooldown (jittered around avg).
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
                return
            avg = self._get_cooldown_avg()
            low = max(1, int(avg * 0.7))
            high = max(low, int(avg * 1.3))
            cooldown = random.randint(low, high)
            account.cooldown_until = time.monotonic() + cooldown

    def disable(self, account: AccountState, reason: str) -> None:
        with self._lock:
            account.disabled_reason = reason

    def all_disabled(self) -> bool:
        return bool(self._accounts) and all(
            a.disabled_reason is not None for a in self._accounts
        )

    def average_cooldown(self) -> float:
        """Current average cooldown seconds (live read of get_cooldown_avg)."""
        return self._get_cooldown_avg()
