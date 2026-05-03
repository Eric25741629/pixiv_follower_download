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
