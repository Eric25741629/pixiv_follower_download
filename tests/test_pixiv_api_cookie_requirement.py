from pathlib import Path
import json
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import pixiv_api


class DummyResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ""

    def json(self):
        if self._payload is None:
            raise ValueError("no payload")
        return self._payload


def _trace_file(appdata_root):
    return os.path.join(appdata_root, "pixiv_download", "pixiv_cookie_requirement.json")


def test_pixiv_info_normalizes_hashed_artwork_id_and_uses_numeric_ajax(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    called = {"ajax_url": None}

    def fake_get(url, headers=None, timeout=20):
        called["ajax_url"] = str(url)
        return DummyResponse(404, payload={"error": True}, text='{"error":true}')

    monkeypatch.setattr(pixiv_api.requests, "get", fake_get)

    ret = pixiv_api.Pixiv_info(
        "https://www.pixiv.net/artworks/140234279-5e696ebeab5c07b269d3d2cdd620b201",
        Agent="UA",
        cookie="PHPSESSID=test",
    )

    assert ret == [404]
    assert called["ajax_url"] == "https://www.pixiv.net/ajax/illust/140234279"


def test_pixiv_info_404_should_store_requires_cookie_none(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    def fake_get(url, headers=None, timeout=20):
        return DummyResponse(404, payload={"error": True}, text='{"error":true}')

    monkeypatch.setattr(pixiv_api.requests, "get", fake_get)

    ret = pixiv_api.Pixiv_info(
        "https://www.pixiv.net/artworks/140234279-abcdef",
        Agent="UA",
        cookie="PHPSESSID=test",
    )
    assert ret == [404]

    path = _trace_file(str(tmp_path))
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entry = data.get("140234279")
    assert isinstance(entry, dict)
    assert entry.get("status_no_cookie") == 404
    assert entry.get("requires_cookie") is None


def test_get_pixiv_cookie_requirement_accepts_hashed_pid(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    base = os.path.join(str(tmp_path), "pixiv_download")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, "pixiv_cookie_requirement.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"140234279": {"requires_cookie": True}}, f, ensure_ascii=False, indent=2)

    val = pixiv_api.get_pixiv_cookie_requirement("140234279-5e696ebeab5c07b269d3d2cdd620b201")
    assert val is True
