"""Regression tests for normalize_cookie_entries — specifically that
status and last_tested_at survive normalization round-trips, since the
cookies view relies on them to render the table after reload."""
from app.core.pixiv_thread_utils import normalize_cookie_entries


def test_preserves_status_when_present():
    raw = [{"cookie": "c1", "alias": "A", "status": "有效"}]
    out = normalize_cookie_entries(raw)
    assert out == [{"cookie": "c1", "alias": "A", "status": "有效"}]


def test_preserves_last_tested_at_when_present():
    raw = [{"cookie": "c1", "alias": "A", "last_tested_at": 1730000000.5}]
    out = normalize_cookie_entries(raw)
    assert out[0]["last_tested_at"] == 1730000000.5


def test_omits_status_field_when_absent():
    raw = [{"cookie": "c1", "alias": "A"}]
    out = normalize_cookie_entries(raw)
    assert out == [{"cookie": "c1", "alias": "A"}]
    assert "status" not in out[0]
    assert "last_tested_at" not in out[0]


def test_dedupe_carries_status_forward():
    # Duplicate cookies: first has no status, second has 有效 — the merged
    # entry should pick up the status from the duplicate.
    raw = [
        {"cookie": "c1", "alias": "A"},
        {"cookie": "c1", "alias": "", "status": "有效", "last_tested_at": 100.0},
    ]
    out = normalize_cookie_entries(raw)
    assert len(out) == 1
    assert out[0]["status"] == "有效"
    assert out[0]["last_tested_at"] == 100.0


def test_plain_string_input_has_no_status():
    out = normalize_cookie_entries(["c1", "c2"])
    assert all("status" not in e for e in out)
    assert all("last_tested_at" not in e for e in out)
