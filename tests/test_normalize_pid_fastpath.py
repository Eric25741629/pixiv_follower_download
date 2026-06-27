"""The bare-digit fast path in ``normalize_pid`` must be exactly equivalent
to the original transform-based implementation for every input shape.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pixiv_thread_utils import normalize_pid


def _legacy_normalize_pid(value):
    """The pre-optimisation implementation, kept here as the oracle."""
    s = str(value).strip()
    if not s:
        return ""
    if "_" in s:
        s = s.split("_", 1)[0]
    s = s.replace("p0", "")
    m = re.search(r"\d+", s)
    if m:
        return m.group(0)
    return s


CASES = [
    "12345", "0", "139112835", "  42  ", "",
    "12345_p0", "12345_p3", "139112835-c476a4d0_p0",
    "123p0", "p0", "PID137328754", "abc", "12_34",
    "100p00", "9999999999999",
]


def test_fast_path_equivalent_to_legacy():
    for case in CASES:
        assert normalize_pid(case) == _legacy_normalize_pid(case), case


def test_pure_digits_returned_verbatim():
    assert normalize_pid("139112835") == "139112835"
    assert normalize_pid("100") == "100"
