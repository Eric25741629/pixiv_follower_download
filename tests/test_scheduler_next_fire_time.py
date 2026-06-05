import os, sys, datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.scheduler_service import compute_next_fire


def test_daily_today_not_yet_passed():
    now = datetime.datetime(2026, 6, 5, 1, 0, 0)
    cfg = {"mode": "daily", "time": "03:00", "interval_hours": 6}
    nxt = compute_next_fire(now, cfg, None)
    assert nxt == datetime.datetime(2026, 6, 5, 3, 0, 0)


def test_daily_today_already_passed_rolls_to_tomorrow():
    now = datetime.datetime(2026, 6, 5, 4, 0, 0)
    cfg = {"mode": "daily", "time": "03:00", "interval_hours": 6}
    nxt = compute_next_fire(now, cfg, None)
    assert nxt == datetime.datetime(2026, 6, 6, 3, 0, 0)


def test_interval_with_last_fire():
    now = datetime.datetime(2026, 6, 5, 4, 0, 0)
    cfg = {"mode": "interval", "time": "03:00", "interval_hours": 6}
    last = datetime.datetime(2026, 6, 5, 1, 0, 0)
    nxt = compute_next_fire(now, cfg, last)
    assert nxt == datetime.datetime(2026, 6, 5, 7, 0, 0)


def test_interval_without_last_fire_starts_one_period_out():
    now = datetime.datetime(2026, 6, 5, 4, 0, 0)
    cfg = {"mode": "interval", "time": "03:00", "interval_hours": 6}
    nxt = compute_next_fire(now, cfg, None)
    assert nxt == datetime.datetime(2026, 6, 5, 10, 0, 0)


def test_bad_time_falls_back_to_midnight():
    now = datetime.datetime(2026, 6, 5, 1, 0, 0)
    cfg = {"mode": "daily", "time": "not-a-time", "interval_hours": 6}
    nxt = compute_next_fire(now, cfg, None)
    assert nxt.hour == 0 and nxt.minute == 0
