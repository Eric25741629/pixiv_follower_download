"""Tests for the static/pure helpers extracted from download_thread.__init__."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


# ── _resolve_intra_pid_wait_range (static, pure) ────────────────────────────

def test_resolve_intra_pid_wait_range_normal():
    assert download_thread._resolve_intra_pid_wait_range(2, 8) == (2, 8)


def test_resolve_intra_pid_wait_range_swap_when_max_below_min():
    """When max < min, max is bumped up to min — never the other way around."""
    lo, hi = download_thread._resolve_intra_pid_wait_range(10, 5)
    assert lo == 10
    assert hi == 10


def test_resolve_intra_pid_wait_range_clamps_negative_min_to_zero():
    lo, hi = download_thread._resolve_intra_pid_wait_range(-3, 8)
    assert lo == 0
    assert hi == 8


def test_resolve_intra_pid_wait_range_falls_back_on_garbage():
    lo, hi = download_thread._resolve_intra_pid_wait_range("abc", "xyz")
    assert lo == 1
    assert hi == 3


def test_resolve_intra_pid_wait_range_falls_back_on_none():
    lo, hi = download_thread._resolve_intra_pid_wait_range(None, None)
    assert lo == 1
    assert hi == 3


def test_resolve_intra_pid_wait_range_handles_string_numbers():
    lo, hi = download_thread._resolve_intra_pid_wait_range("4", "9")
    assert lo == 4
    assert hi == 9


def test_resolve_intra_pid_wait_range_zero_zero():
    lo, hi = download_thread._resolve_intra_pid_wait_range(0, 0)
    assert lo == 0
    assert hi == 0
