import json
import os
import tempfile
from app.core.stats_collector import StatsCollector


def test_initial_state():
    sc = StatsCollector()
    s = sc.get_session_stats()
    assert s["bytes"] == 0
    assert s["files_ok"] == 0
    assert s["files_fail"] == 0
    assert s["requests"] == {}
    assert s["jxl_src"] == 0
    assert s["jxl_dst"] == 0


def test_report_bytes():
    sc = StatsCollector()
    sc.report_bytes(1024)
    sc.report_bytes(512)
    assert sc.get_session_stats()["bytes"] == 1536


def test_report_file():
    sc = StatsCollector()
    sc.report_file(True)
    sc.report_file(True)
    sc.report_file(False)
    s = sc.get_session_stats()
    assert s["files_ok"] == 2
    assert s["files_fail"] == 1


def test_report_request():
    sc = StatsCollector()
    sc.report_request("Cookie1")
    sc.report_request("Cookie1")
    sc.report_request("Cookie2")
    s = sc.get_session_stats()
    assert s["requests"]["Cookie1"] == 2
    assert s["requests"]["Cookie2"] == 1


def test_report_jxl():
    sc = StatsCollector()
    sc.report_jxl(1000, 400)
    sc.report_jxl(2000, 800)
    s = sc.get_session_stats()
    assert s["jxl_src"] == 3000
    assert s["jxl_dst"] == 1200


def test_reset_session():
    sc = StatsCollector()
    sc.report_bytes(100)
    sc.report_file(True)
    sc.report_request("Cookie1")
    sc.report_jxl(500, 200)
    sc.reset_session()
    s = sc.get_session_stats()
    assert s["bytes"] == 0
    assert s["files_ok"] == 0
    assert s["requests"] == {}
    assert s["jxl_src"] == 0


class _FakeClock:
    """Controllable stand-in for the ``time`` module (only ``monotonic`` used)."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def monotonic(self) -> float:
        return self.t


def test_elapsed_zero_before_any_run():
    sc = StatsCollector()
    # No reset_session() yet -> timer never started.
    assert sc.get_session_stats()["elapsed"] == 0.0


def test_elapsed_counts_while_running(monkeypatch):
    import app.core.stats_collector as mod
    clock = _FakeClock(1000.0)
    monkeypatch.setattr(mod, "time", clock)
    sc = StatsCollector()
    sc.reset_session()
    clock.t = 1007.0
    assert sc.get_session_stats()["elapsed"] == 7.0


def test_mark_session_end_freezes_elapsed(monkeypatch):
    import app.core.stats_collector as mod
    clock = _FakeClock(1000.0)
    monkeypatch.setattr(mod, "time", clock)
    sc = StatsCollector()
    sc.reset_session()
    clock.t = 1005.0
    sc.mark_session_end()           # freeze at 5s
    clock.t = 1100.0                # wall clock keeps moving...
    assert sc.get_session_stats()["elapsed"] == 5.0  # ...but elapsed is frozen


def test_mark_session_end_is_idempotent(monkeypatch):
    import app.core.stats_collector as mod
    clock = _FakeClock(1000.0)
    monkeypatch.setattr(mod, "time", clock)
    sc = StatsCollector()
    sc.reset_session()
    clock.t = 1005.0
    sc.mark_session_end()
    clock.t = 1009.0
    sc.mark_session_end()           # second call must not advance the frozen end
    assert sc.get_session_stats()["elapsed"] == 5.0


def test_resume_session_continues_from_original_start(monkeypatch):
    import app.core.stats_collector as mod
    clock = _FakeClock(1000.0)
    monkeypatch.setattr(mod, "time", clock)
    sc = StatsCollector()
    sc.reset_session()              # start at 1000
    clock.t = 1005.0
    sc.mark_session_end()           # freeze (intermediate Run-All step end)
    clock.t = 1006.0
    sc.resume_session()             # Run All chains into next step
    clock.t = 1020.0
    # Spans the whole run from 1000, not just the resumed leg.
    assert sc.get_session_stats()["elapsed"] == 20.0


def test_reset_session_clears_frozen_end(monkeypatch):
    import app.core.stats_collector as mod
    clock = _FakeClock(1000.0)
    monkeypatch.setattr(mod, "time", clock)
    sc = StatsCollector()
    sc.reset_session()
    clock.t = 1005.0
    sc.mark_session_end()
    clock.t = 2000.0
    sc.reset_session()              # fresh run restarts at 2000
    clock.t = 2003.0
    assert sc.get_session_stats()["elapsed"] == 3.0


def test_mark_end_and_resume_noop_without_start(monkeypatch):
    import app.core.stats_collector as mod
    clock = _FakeClock(1000.0)
    monkeypatch.setattr(mod, "time", clock)
    sc = StatsCollector()
    # Never started: both are safe no-ops, elapsed stays 0.
    sc.mark_session_end()
    sc.resume_session()
    assert sc.get_session_stats()["elapsed"] == 0.0


def test_save_and_load(tmp_path):
    path = str(tmp_path / "stats.json")
    sc = StatsCollector(path)
    sc.report_bytes(999)
    sc.report_file(True)
    sc.report_request("CookieA")
    sc.report_jxl(100, 50)
    sc.save()

    sc2 = StatsCollector(path)
    lt = sc2.get_lifetime_stats()
    assert lt["lifetime_bytes_downloaded"] == 999
    assert lt["lifetime_files_downloaded"] == 1
    assert lt["cookie_request_counts"]["CookieA"] == 1
    assert lt["lifetime_jxl_src"] == 100
    assert lt["lifetime_jxl_dst"] == 50


def test_save_accumulates(tmp_path):
    path = str(tmp_path / "stats.json")
    sc = StatsCollector(path)
    sc.report_bytes(100)
    sc.report_file(True)
    sc.report_request("Cookie1")
    sc.report_jxl(50, 20)
    sc.save()

    sc2 = StatsCollector(path)
    sc2.report_bytes(200)
    sc2.report_file(True)
    sc2.report_request("Cookie1")
    sc2.report_jxl(60, 30)
    sc2.save()

    sc3 = StatsCollector(path)
    lt = sc3.get_lifetime_stats()
    assert lt["lifetime_bytes_downloaded"] == 300
    assert lt["lifetime_files_downloaded"] == 2
    assert lt["lifetime_sessions"] == 2
    assert lt["cookie_request_counts"]["Cookie1"] == 2
    assert lt["lifetime_jxl_src"] == 110
    assert lt["lifetime_jxl_dst"] == 50


def test_load_missing_file():
    sc = StatsCollector("/nonexistent/path/stats.json")
    lt = sc.get_lifetime_stats()
    assert lt["lifetime_bytes_downloaded"] == 0
    assert lt["lifetime_sessions"] == 0


def test_load_corrupt_file(tmp_path):
    path = str(tmp_path / "stats.json")
    with open(path, "w") as f:
        f.write("not valid json{{{")
    sc = StatsCollector(path)
    lt = sc.get_lifetime_stats()
    assert lt["lifetime_bytes_downloaded"] == 0


def test_format_bytes():
    sc = StatsCollector()
    assert sc.format_bytes(0) == "0 B"
    assert sc.format_bytes(1023) == "1023 B"
    assert sc.format_bytes(1024) == "1.00 KB"
    assert sc.format_bytes(1048576) == "1.00 MB"
    assert sc.format_bytes(1073741824) == "1.00 GB"
