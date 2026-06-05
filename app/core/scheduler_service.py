"""In-app scheduler: fire Run All on a daily-time or interval schedule.

compute_next_fire is a pure function (no clock access) so it is unit-testable;
SchedulerService is a daemon thread that sleeps until the next fire time and
invokes a callback, skipping when a run is already active.
"""
from __future__ import annotations

import datetime
import threading  # noqa: F401  # used by SchedulerService (added in Task 3)


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
