"""Tests for _build_filter_meta_entry and _passes_pid_filter rule helpers."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


def _stub():
    t = download_thread.__new__(download_thread)
    t._ban_tag_norm = []
    t._must_tag_norm = []
    t.like_num = 0
    t.special_like_rules = []
    t._step4_filter_skip_counts = {"tag": 0, "like": 0, "no_meta": 0}
    t._step4_filter_skip_notice_emitted = False
    t._step4_filter_skip_every = 200
    t._pid_filter_decision = {}
    t._tag_hit = lambda key, tags: any(str(key).lower() in str(t).lower() for t in tags)
    t._to_int = lambda v, default=None: int(v) if v is not None else default
    return t


# ── _build_filter_meta_entry ────────────────────────────────────────────────

def test_build_filter_meta_entry_full_shape():
    t = _stub()
    entry = t._build_filter_meta_entry(
        "https://www.pixiv.net/artworks/12345",
        ["tag1", "tag2"], 100, 3, "https://i.pximg.net/x_p.jpg", True,
    )
    assert entry["tag"] == ["tag1", "tag2"]
    assert entry["like"] == 100
    assert entry["pagecount"] == 3
    assert entry["img_url"] == "https://i.pximg.net/x_p.jpg"
    assert entry["requires_cookie"] is True
    assert entry["artwork_url"] == "https://www.pixiv.net/artworks/12345"
    assert entry["pixiv_info"]["source"] == "filter_fetch"


def test_build_filter_meta_entry_handles_non_list_tag():
    """When Pixiv_info returns a sentinel like 404 instead of a list, fall back to []."""
    t = _stub()
    entry = t._build_filter_meta_entry(
        "url", 404, 0, 0, "None", None,
    )
    assert entry["tag"] == []
    assert entry["pixiv_info"]["tag"] == []
    assert entry["requires_cookie"] is None


# ── _filter_blocked_by_ban_tag ──────────────────────────────────────────────

def test_blocked_by_ban_tag_match():
    t = _stub()
    t._ban_tag_norm = ["adult"]
    assert t._filter_blocked_by_ban_tag(["adult", "オリジナル"]) is True


def test_blocked_by_ban_tag_no_match():
    t = _stub()
    t._ban_tag_norm = ["adult"]
    assert t._filter_blocked_by_ban_tag(["safe", "オリジナル"]) is False


def test_blocked_by_ban_tag_no_ban_list():
    t = _stub()
    t._ban_tag_norm = []
    assert t._filter_blocked_by_ban_tag(["whatever"]) is False


# ── _filter_missing_must_tag ────────────────────────────────────────────────

def test_missing_must_tag_when_none_match():
    t = _stub()
    t._must_tag_norm = ["female"]
    assert t._filter_missing_must_tag(["male", "art"]) is True


def test_missing_must_tag_satisfied_when_one_matches():
    """The OR semantic: at least ONE must_tag must match."""
    t = _stub()
    t._must_tag_norm = ["female", "male"]
    assert t._filter_missing_must_tag(["male", "art"]) is False


def test_missing_must_tag_no_must_list():
    """Empty must_tag list means no filter — never blocks."""
    t = _stub()
    t._must_tag_norm = []
    assert t._filter_missing_must_tag(["anything"]) is False


# ── _filter_below_like_threshold ────────────────────────────────────────────

def test_below_like_threshold_when_under_min():
    t = _stub()
    t.like_num = 100
    assert t._filter_below_like_threshold(["safe"], 50) is True


def test_below_like_threshold_when_at_or_above():
    t = _stub()
    t.like_num = 100
    assert t._filter_below_like_threshold(["safe"], 100) is False
    assert t._filter_below_like_threshold(["safe"], 999) is False


def test_below_like_threshold_no_threshold():
    """No like_num set → never below."""
    t = _stub()
    t.like_num = 0
    assert t._filter_below_like_threshold(["safe"], 0) is False


def test_below_like_threshold_unknown_like_value():
    """When like is None (no meta), don't claim below — only the no_meta path filters."""
    t = _stub()
    t.like_num = 100
    assert t._filter_below_like_threshold(["safe"], None) is False


# ── _filter_no_meta_decision ────────────────────────────────────────────────

def test_no_meta_decision_skip_with_like_filter():
    t = _stub()
    t.like_num = 50
    decision = t._filter_no_meta_decision()
    assert decision == (False, "no_meta")


def test_no_meta_decision_keep_pending_without_filter():
    t = _stub()
    t.like_num = 0
    t.special_like_rules = []
    decision = t._filter_no_meta_decision()
    assert decision == (True, "no_meta")


def test_no_meta_decision_skip_with_special_rule():
    """Even when like_num is 0, presence of special_like_rules triggers the skip path."""
    t = _stub()
    t.like_num = 0
    t.special_like_rules = [{"tag": "x", "like": 100}]
    decision = t._filter_no_meta_decision()
    assert decision == (False, "no_meta")
