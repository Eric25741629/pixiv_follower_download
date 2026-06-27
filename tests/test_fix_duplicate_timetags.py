"""Tests for tools/fix_duplicate_timetags.py (repairing duplicate timetag
filename prefixes left by the missing timechanged persistence)."""
import datetime
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.fix_duplicate_timetags import collect, main, plan_renames


def _mk(p, name):
    f = p / name
    f.write_bytes(b"x")
    return f


def test_plan_keeps_first_and_advances_duplicates(tmp_path):
    _mk(tmp_path, "20260330_084355_PID1_a.jpg")
    _mk(tmp_path, "20260330_084355_PID2_b.jpg")
    _mk(tmp_path, "20260330_084356_PID3_c.jpg")  # occupies the next second
    renames = plan_renames(collect(str(tmp_path)))
    # one duplicate -> re-stamped past the occupied 084356 to 084357
    assert len(renames) == 1
    _d, old, new, tag = renames[0]
    assert old.startswith("20260330_084355_")
    assert tag == "20260330_084357"
    assert new == "20260330_084357" + old[len("20260330_084355"):]


def test_apply_renames_and_sets_mtime(tmp_path):
    _mk(tmp_path, "20260330_084355_PID1_a.jpg")
    _mk(tmp_path, "20260330_084355_PID2_b.jpg")
    rc = main(["--root", str(tmp_path), "--apply"])
    assert rc == 0
    names = sorted(os.listdir(tmp_path))
    assert names == [
        "20260330_084355_PID1_a.jpg",
        "20260330_084356_PID2_b.jpg",
    ]
    expected = datetime.datetime(2026, 3, 30, 8, 43, 56).timestamp()
    assert abs(os.path.getmtime(tmp_path / names[1]) - expected) < 2


def test_dry_run_changes_nothing(tmp_path):
    _mk(tmp_path, "20260330_084355_PID1_a.jpg")
    _mk(tmp_path, "20260330_084355_PID2_b.jpg")
    before = sorted(os.listdir(tmp_path))
    rc = main(["--root", str(tmp_path)])
    assert rc == 0
    assert sorted(os.listdir(tmp_path)) == before


def test_non_timetag_files_ignored(tmp_path):
    _mk(tmp_path, "PID999_no_time.jpg")
    assert collect(str(tmp_path)) == []
