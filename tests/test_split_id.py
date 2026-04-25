"""Behavior-preserving tests for download_thread.splitID (Phase 20)."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


def _thread():
    return download_thread.__new__(download_thread)


def test_split_id_extracts_from_pid_equals_format():
    t = _thread()
    result = t.splitID(["PID=12345_p0.jpg"])
    assert "12345" in result


def test_split_id_extracts_from_pid_space_format():
    t = _thread()
    result = t.splitID(["something PID12345 extra.jpg"])
    assert "12345" in result


def test_split_id_extracts_from_illust_prefix():
    t = _thread()
    result = t.splitID(["illust_12345_p0.jpg"])
    assert "12345" in result


def test_split_id_extracts_from_illust_timestamp_format():
    t = _thread()
    result = t.splitID(["illust_44773280_20220413_040534.jpg"])
    assert "44773280" in result


def test_split_id_skips_non_image_files():
    t = _thread()
    result = t.splitID(["PID=12345_p0.txt", "PID=67890_p0.pdf"])
    assert result == []


def test_split_id_skips_files_without_pid_or_illust_marker():
    t = _thread()
    result = t.splitID(["random_name_12345.jpg", "photo.png"])
    assert result == []


def test_split_id_dedupes_results():
    t = _thread()
    result = t.splitID([
        "PID=12345_p0.jpg",
        "PID=12345_p1.jpg",
        "PID=12345_p2.jpg",
    ])
    assert result.count("12345") == 1


def test_split_id_accepts_png_and_gif():
    t = _thread()
    result = t.splitID([
        "PID=11111_p0.jpg",
        "PID=22222_p0.png",
        "PID=33333_p0.gif",
    ])
    assert set(result) >= {"11111", "22222", "33333"}


def test_split_id_rejects_too_short_ids():
    # Branch 1 requires len(id) > 4; "1234" is len 4.
    t = _thread()
    result = t.splitID(["PID=1234_p0.jpg"])
    assert "1234" not in result


def test_split_id_rejects_too_long_ids():
    # Branch 1 requires len(id) < 12; "123456789012" is len 12.
    t = _thread()
    result = t.splitID(["PID=123456789012_p0.jpg"])
    assert "123456789012" not in result


def test_split_id_empty_list():
    t = _thread()
    assert t.splitID([]) == []


def test_split_id_mixed_batch():
    t = _thread()
    files = [
        "PID=11111_p0.jpg",
        "illust_22222_p0.png",
        "illust_33333333_20220101_120000.jpg",
        "something PID44444 other.jpg",
        "random.txt",
        "no_markers.jpg",
    ]
    result = t.splitID(files)
    assert "11111" in result
    assert "22222" in result
    assert "33333333" in result
    assert "44444" in result
