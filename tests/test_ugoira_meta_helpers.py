"""Tests for the helpers extracted from gif_download's metadata block."""
from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


# ── _parse_ugoira_meta_payload (static, pure) ───────────────────────────────

class _FakeResponse:
    def __init__(self, body):
        if isinstance(body, str):
            self.content = body.encode("utf-8")
            self.text = body
        else:
            self.content = json.dumps(body).encode("utf-8")
            self.text = json.dumps(body)


def test_parse_ugoira_meta_payload_extracts_url_and_delays():
    payload = {"body": {
        "originalSrc": "https://i.pximg.net/x_ugoira1920x1080.zip",
        "frames": [{"delay": 100}, {"delay": 200}, {"delay": 300}],
    }}
    out = download_thread._parse_ugoira_meta_payload(_FakeResponse(payload), "12345")
    assert out is not None
    download_url, delay_info = out
    assert download_url == "https://i.pximg.net/x_ugoira1920x1080.zip"
    assert delay_info == [100, 200, 300]


def test_parse_ugoira_meta_payload_returns_none_on_invalid_json():
    out = download_thread._parse_ugoira_meta_payload(
        _FakeResponse("not json"), "12345"
    )
    assert out is None


def test_parse_ugoira_meta_payload_returns_none_when_body_missing():
    out = download_thread._parse_ugoira_meta_payload(
        _FakeResponse({"error": True}), "12345"
    )
    assert out is None


def test_parse_ugoira_meta_payload_returns_none_when_originalsrc_missing():
    out = download_thread._parse_ugoira_meta_payload(
        _FakeResponse({"body": {"frames": [{"delay": 100}]}}), "12345"
    )
    assert out is None


def test_parse_ugoira_meta_payload_returns_none_when_frames_misshapen():
    out = download_thread._parse_ugoira_meta_payload(
        _FakeResponse({"body": {
            "originalSrc": "u",
            "frames": [{"NO_DELAY_KEY": 100}],
        }}), "12345"
    )
    assert out is None


def test_parse_ugoira_meta_payload_handles_empty_frames():
    out = download_thread._parse_ugoira_meta_payload(
        _FakeResponse({"body": {
            "originalSrc": "https://x",
            "frames": [],
        }}), "12345"
    )
    assert out is not None
    download_url, delay_info = out
    assert download_url == "https://x"
    assert delay_info == []


# ── _maybe_mark_meta_retry_cookie ───────────────────────────────────────────

def _stub_for_retry():
    t = download_thread.__new__(download_thread)
    t._mark_calls = []
    t._mark_gif_cookie_usage = lambda pid, used, source="": (
        t._mark_calls.append((pid, used, source))
    )
    return t


def test_retry_cookie_marked_when_used_and_status_200():
    t = _stub_for_retry()
    used = t._maybe_mark_meta_retry_cookie(
        "1", {"retry_used": True, "retry_with_cookie_status": 200}
    )
    assert used is True
    assert t._mark_calls == [("1", True, "ugoira_meta_retry")]


def test_retry_cookie_not_marked_when_retry_unused():
    t = _stub_for_retry()
    used = t._maybe_mark_meta_retry_cookie(
        "1", {"retry_used": False, "retry_with_cookie_status": 200}
    )
    assert used is False
    assert t._mark_calls == []


def test_retry_cookie_not_marked_when_status_not_200():
    t = _stub_for_retry()
    used = t._maybe_mark_meta_retry_cookie(
        "1", {"retry_used": True, "retry_with_cookie_status": 403}
    )
    assert used is False
    assert t._mark_calls == []


def test_retry_cookie_handles_missing_keys():
    t = _stub_for_retry()
    assert t._maybe_mark_meta_retry_cookie("1", {}) is False
    assert t._mark_calls == []


def test_retry_cookie_handles_garbage_status():
    t = _stub_for_retry()
    assert t._maybe_mark_meta_retry_cookie(
        "1", {"retry_used": True, "retry_with_cookie_status": "abc"}
    ) is False
    assert t._mark_calls == []
