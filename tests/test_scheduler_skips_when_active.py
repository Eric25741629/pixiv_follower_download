import os, sys, datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.scheduler_service import SchedulerService


def test_fire_once_skips_when_run_active():
    calls = {"n": 0}
    svc = SchedulerService(
        get_cfg=lambda: {"enabled": True, "mode": "daily", "time": "03:00"},
        run_all=lambda: calls.__setitem__("n", calls["n"] + 1),
        is_active=lambda: True,  # a run is in progress
    )
    fired = svc._fire_if_due(now=datetime.datetime(2026, 6, 5, 3, 0, 1),
                             due=datetime.datetime(2026, 6, 5, 3, 0, 0))
    assert fired is False
    assert calls["n"] == 0


def test_fire_once_runs_when_idle_and_due():
    calls = {"n": 0}
    svc = SchedulerService(
        get_cfg=lambda: {"enabled": True, "mode": "daily", "time": "03:00"},
        run_all=lambda: calls.__setitem__("n", calls["n"] + 1),
        is_active=lambda: False,
    )
    fired = svc._fire_if_due(now=datetime.datetime(2026, 6, 5, 3, 0, 1),
                             due=datetime.datetime(2026, 6, 5, 3, 0, 0))
    assert fired is True
    assert calls["n"] == 1


def test_not_due_does_not_fire():
    calls = {"n": 0}
    svc = SchedulerService(
        get_cfg=lambda: {"enabled": True, "mode": "daily", "time": "03:00"},
        run_all=lambda: calls.__setitem__("n", calls["n"] + 1),
        is_active=lambda: False,
    )
    fired = svc._fire_if_due(now=datetime.datetime(2026, 6, 5, 2, 0, 0),
                             due=datetime.datetime(2026, 6, 5, 3, 0, 0))
    assert fired is False
    assert calls["n"] == 0
