"""Download pacing / countdown helpers for ``download_thread`` (file-size refactor).

The polite inter-PID and intra-PID wait loop, its 1-second countdown tick, the
'等待 N 秒' header emit, the randomized delay calc, and the human size
formatter. Mixed into ``download_thread`` via ``_Step4PacingMixin``; every
method reaches worker state (``self._q`` / ``self._stop_event`` /
``self._pause_event`` / ``self._scheduler`` / the intra-PID wait bounds /
``self._is_cookie_used_for_pid``) through inheritance, so behaviour is
unchanged. ``tests/test_step4_download_helpers.py`` pins the countdown path.
"""
from __future__ import annotations

import contextlib
import random as pyrandom

from app.core import diag_log
from app.core.worker_event import WorkerEvent


class _Step4PacingMixin:
    def _format_size_human(self, value):
        try:
            size = int(value or 0)
        except Exception:
            size = 0
        sign = "-" if size < 0 else ""
        n = abs(size)
        if n < 1000:
            return f"{sign}{n} B"
        units = [
            ("GB", 1000 ** 3),
            ("MB", 1000 ** 2),
            ("KB", 1000),
        ]
        for unit, factor in units:
            if n >= factor:
                return f"{sign}{float(n) / float(factor):.2f} {unit}"
        return f"{sign}{n} B"

    def _emit_countdown_start_log(self, pid, delay, label, color):
        """Print the '[下載等待][label] 等待 N 秒' header at the start of a wait."""
        cookie_used = self._is_cookie_used_for_pid(pid)
        ratio_text = '1.0x' if cookie_used else '0.5x'
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output",
                f"<p><font color='{color}'>[下載等待][{label}] 等待 {delay} 秒 "
                f"(PID {pid}, 倍率 {ratio_text}, cookie_used={cookie_used})</font></p>"
            ))

    def _countdown_tick(self, remaining, respect_group_stop):
        """Run one 1-second tick. Returns True iff the loop should break."""
        if self._stop_event.is_set():
            return True
        if respect_group_stop and self._stop_after_group:
            return True
        while not self._pause_event.is_set():
            if self._stop_event.is_set():
                return True
            self._pause_event.wait(timeout=0.5)
        if self._stop_event.is_set():
            return True
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("countdown", remaining))
        return self._stop_event.wait(timeout=1.0)

    def _run_download_countdown(self, pid, min_sec, max_sec, *, label, color, respect_group_stop):
        delay = self._calc_sleep_delay(min_sec, max_sec, pid=pid)
        mode = "single mode" if self.single_mode_flag else "pool mode"
        diag_log.log(diag_log.WORKER,
                     f"PID {pid} _run_download_countdown[{label}] {delay}s ({mode})")
        self._emit_countdown_start_log(pid, delay, label, color)
        for remaining in range(int(delay), 0, -1):
            if self._countdown_tick(remaining, respect_group_stop):
                break
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("countdown", 0))

    def _sleep_between_downloads(self, pid):
        # Inter-PID cooldown is owned by AccountScheduler.release() when active.
        if self._scheduler is not None:
            return
        avg = int(getattr(self, "_legacy_pid_cooldown_avg", 35))
        low = max(1, int(avg * 0.7))
        high = max(low, int(avg * 1.3))
        delay = pyrandom.randint(low, high)
        self._run_download_countdown(
            pid,
            delay,
            delay,
            label="PID間",
            color="green",
            respect_group_stop=True,
        )

    def _sleep_within_pid(self, pid):
        # Wait between pages within the same PID.
        self._run_download_countdown(
            pid,
            self.intra_pid_wait_min,
            self.intra_pid_wait_max,
            label="同PID",
            color="gray",
            respect_group_stop=False,
        )

    def _calc_sleep_delay(self, min_sec, max_sec, pid=None):
        """Calculate randomized sleep delay between min_sec and max_sec.

        The scheduler-aware path no longer applies cookie-pool speedup; this
        function is now used only for intra-PID polite delays.
        """
        return pyrandom.randint(int(min_sec), int(max_sec))
