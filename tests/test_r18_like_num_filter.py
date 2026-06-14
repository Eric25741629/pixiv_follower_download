"""Regression test for the r18_like_num like-filter wiring.

`_r18_aware_like_base` raises the effective minimum-like base to
``r18_like_num`` for R-18 (non-R-18G) artworks when that value is stricter than
``like_num``. Default ``r18_like_num=0`` is a pure no-op (returns ``like_num``
unchanged), so behavior is byte-identical to before for every existing user.
"""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


def _stub(like_num=100, r18_like_num=500):
    """Minimal download_thread with just the attrs the filter helpers touch.

    Built via __new__ so the inherited helpers (_r18_aware_like_base on the
    PauseableThread base, plus _tag_hit / _to_int) are reachable without running
    __init__'s heavy DB/folder scan.
    """
    t = download_thread.__new__(download_thread)
    t.like_num = like_num
    t.r18_like_num = r18_like_num
    t.special_like_rules = []
    t._ban_tag_norm = []
    t._must_tag_norm = []
    t._tag_hit = lambda key, tags: any(str(key).lower() in str(x).lower() for x in tags)
    t._to_int = lambda v, default=None: int(v) if v is not None else default
    return t


def test_r18_like_num_filter():
    # ── _r18_aware_like_base classification ──────────────────────────────────
    strict = _stub(like_num=100, r18_like_num=500)
    # R-18 work: base raised to the stricter R-18 threshold.
    assert strict._r18_aware_like_base(["r-18"]) == 500
    # Non-R-18 work: base stays at like_num.
    assert strict._r18_aware_like_base(["original"]) == 100
    # R-18G is gore-adjacent; it must NOT be treated as plain R-18.
    assert strict._r18_aware_like_base(["r-18g"]) == 100
    # When r18_like_num <= like_num it is never stricter -> no-op.
    assert _stub(like_num=500, r18_like_num=300)._r18_aware_like_base(["r-18"]) == 500

    # ── _filter_below_like_threshold behavior (stricter R-18 base) ───────────
    # like=300: an R-18 work is below the 500 R-18 base (filtered) but a
    # non-R-18 work clears the base 100 (kept).
    assert strict._filter_below_like_threshold(["r-18"], 300) is True
    assert strict._filter_below_like_threshold(["original"], 300) is False

    # ── default stub (r18_like_num=0) is a pure no-op (zero regression) ──────
    noop = _stub(like_num=100, r18_like_num=0)
    assert noop._r18_aware_like_base(["r-18"]) == 100
    assert noop._r18_aware_like_base(["original"]) == 100
    # R-18 and non-R-18 are treated identically once the R-18 raise is off.
    assert noop._filter_below_like_threshold(["r-18"], 300) is False
    assert noop._filter_below_like_threshold(["original"], 300) is False
    assert noop._filter_below_like_threshold(["r-18"], 50) is True
    assert noop._filter_below_like_threshold(["original"], 50) is True
