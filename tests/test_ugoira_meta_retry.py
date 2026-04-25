from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pixiv_thread_utils import fetch_with_cookie_retry


class DummyResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_ugoira_meta_retry_404_then_200():
    calls = []

    def fake_get(url, headers=None, verify=False, stream=True):
        calls.append(dict(headers or {}))
        if len(calls) == 1:
            return DummyResponse(404, '{"error":true,"body":[]}')
        return DummyResponse(200, '{"error":false,"body":{"originalSrc":"ok"}}')

    final_resp, trace, first_resp = fetch_with_cookie_retry(
        http_get=fake_get,
        url="https://www.pixiv.net/ajax/illust/108616429/ugoira_meta?lang=zh_tw",
        headers={"User-Agent": "UA", "Referer": "https://www.pixiv.net/artworks/108616429"},
        cookies="PHPSESSID=abc",
        retry_statuses=(403, 404),
    )

    assert trace["first_try_status"] == 404
    assert trace["retry_used"] is True
    assert trace["retry_with_cookie_status"] == 200
    assert final_resp.status_code == 200
    assert first_resp is not None
    assert len(calls) == 2
    assert "Cookie" not in calls[0]
    assert calls[1].get("Cookie") == "PHPSESSID=abc"


def test_ugoira_meta_retry_404_then_404():
    calls = []

    def fake_get(url, headers=None, verify=False, stream=True):
        calls.append(dict(headers or {}))
        return DummyResponse(404, '{"error":true,"body":[]}')

    final_resp, trace, first_resp = fetch_with_cookie_retry(
        http_get=fake_get,
        url="https://www.pixiv.net/ajax/illust/108616429/ugoira_meta?lang=zh_tw",
        headers={"User-Agent": "UA"},
        cookies="PHPSESSID=abc",
        retry_statuses=(403, 404),
    )

    assert trace["first_try_status"] == 404
    assert trace["retry_used"] is True
    assert trace["retry_with_cookie_status"] == 404
    assert final_resp.status_code == 404
    assert first_resp is not None
    assert len(calls) == 2


def test_ugoira_meta_no_retry_on_200():
    calls = []

    def fake_get(url, headers=None, verify=False, stream=True):
        calls.append(dict(headers or {}))
        return DummyResponse(200, '{"error":false}')

    final_resp, trace, first_resp = fetch_with_cookie_retry(
        http_get=fake_get,
        url="https://www.pixiv.net/ajax/illust/108616429/ugoira_meta?lang=zh_tw",
        headers={"User-Agent": "UA"},
        cookies="PHPSESSID=abc",
        retry_statuses=(403, 404),
    )

    assert final_resp.status_code == 200
    assert trace["first_try_status"] == 200
    assert trace["retry_used"] is False
    assert trace["retry_with_cookie_status"] is None
    assert first_resp is None
    assert len(calls) == 1
