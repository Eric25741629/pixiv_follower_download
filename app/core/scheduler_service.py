"""In-app scheduler: fire Run All on a daily-time or interval schedule.

compute_next_fire is a pure function (no clock access) so it is unit-testable;
SchedulerService is a daemon thread that sleeps until the next fire time and
invokes a callback, skipping when a run is already active.
"""
from __future__ import annotations

import datetime
import threading


def _parse_hhmm(text: str) -> tuple[int, int]:
    """Parse 'HH:MM' -> (hour, minute); fall back to (0, 0) on any error."""
    try:
        hh, mm = str(text).strip().split(":", 1)
        h, m = int(hh), int(mm)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except Exception:
        pass
    return 0, 0


def compute_next_fire(now, cfg, last_fire):
    """Return the next datetime the scheduler should fire.

    daily:    today at cfg['time'] if still in the future, else tomorrow.
    interval: last_fire + interval_hours, or now + interval_hours when there
              is no last_fire yet.
    """
    mode = str(cfg.get("mode", "daily"))
    if mode == "interval":
        try:
            hours = max(1, int(cfg.get("interval_hours", 6)))
        except (TypeError, ValueError):
            hours = 6
        base = last_fire or now
        return base + datetime.timedelta(hours=hours)
    # daily
    h, m = _parse_hhmm(cfg.get("time", "03:00"))
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + datetime.timedelta(days=1)
    return candidate


class SchedulerService:
    """Daemon thread firing ``run_all`` on the configured schedule.

    Constructor takes callables so it stays decoupled from GUI/CLI:
      get_cfg()   -> the live ``schedule`` settings dict
      run_all()   -> trigger a Run-All (same callback the button uses)
      is_active() -> True when a run is already in progress (skip if so)
    """

    def __init__(self, get_cfg, run_all, is_active, emit=None):
        self._get_cfg = get_cfg
        self._run_all = run_all
        self._is_active = is_active
        self._emit = emit or (lambda msg: None)
        self._stop = threading.Event()
        self._thread = None
        self._last_fire = None

    def _fire_if_due(self, now, due):
        """Pure-ish decision: fire run_all iff now >= due and no run is active.
        Returns True iff run_all was invoked."""
        if now < due:
            return False
        if self._is_active():
            self._emit("<p><font color='gray'>排程時間到，但已有任務執行中，略過本次</font></p>")
            self._last_fire = now
            return False
        self._last_fire = now
        try:
            self._run_all()
        except Exception as exc:
            # Surface the failure in the same log channel as the skip notice —
            # a silently-swallowed run_all left the user with no diagnostic and
            # still advanced _last_fire, costing a whole scheduled cycle.
            self._emit(
                f"<p><font color='red'>排程執行失敗：{exc}</font></p>"
            )
            return False
        return True

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        import datetime as _dt
        while not self._stop.is_set():
            cfg = self._get_cfg() or {}
            if not bool(cfg.get("enabled", False)):
                self._stop.wait(30)
                continue
            now = _dt.datetime.now()
            due = compute_next_fire(now, cfg, self._last_fire)
            # Sleep in <=30s slices so config/stop changes are picked up fast.
            wait_s = max(0.0, (due - now).total_seconds())
            self._stop.wait(min(30.0, wait_s) if wait_s > 0 else 0.5)
            if self._stop.is_set():
                break
            self._fire_if_due(_dt.datetime.now(), due)
