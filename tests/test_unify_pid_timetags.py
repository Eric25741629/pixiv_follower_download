"""tools/unify_pid_timetags.py: within-PID prefix unification planning."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.unify_pid_timetags import collect_by_pid, plan_renames


def test_split_pid_pages_unified_to_earliest_tag(tmp_path):
    (tmp_path / "20260605_023146_PID122820069p0 tag.jxl").write_bytes(b"a")
    (tmp_path / "20260605_072511_PID122820069p1 tag.jxl").write_bytes(b"b")
    (tmp_path / "20260101_000000_PID111p0.jpg").write_bytes(b"c")  # consistent PID
    by_pid, total = collect_by_pid(str(tmp_path))
    assert total == 3
    renames, split_pids = plan_renames(by_pid)
    assert split_pids == 1
    assert renames == [(str(tmp_path),
                        "20260605_072511_PID122820069p1 tag.jxl",
                        "20260605_023146_PID122820069p1 tag.jxl",
                        "20260605_023146")]


def test_files_without_pid_or_prefix_ignored(tmp_path):
    (tmp_path / "no_prefix_PID123p0.jpg").write_bytes(b"a")
    (tmp_path / "20260101_000000_noPidHere.jpg").write_bytes(b"b")
    by_pid, total = collect_by_pid(str(tmp_path))
    assert total == 0
    assert by_pid == {}
