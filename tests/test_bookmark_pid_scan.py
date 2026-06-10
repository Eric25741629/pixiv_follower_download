"""Bookmark PID source helpers on the Step 2 worker."""
import os
import sys
from queue import Queue

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.core.thread_pid_scan import get_pixiv_author_imgID_Thread


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _make_thread(tmp_path):
    return get_pixiv_author_imgID_Thread(
        Queue(),
        [],
        "UA",
        str(tmp_path),
        [{"cookie": "c1", "alias": "main"}],
        set(),
        single_thread_mode=True,
        source_mode="bookmarks",
        bookmark_scope="all",
        bookmark_user_id="42",
    )


def test_bookmark_scope_maps_to_pixiv_rest_values(tmp_path):
    t = _make_thread(tmp_path)
    assert t._bookmark_rest_values("public") == ["show"]
    assert t._bookmark_rest_values("private") == ["hide"]
    assert t._bookmark_rest_values("all") == ["show", "hide"]
    assert t._bookmark_rest_values("bad") == ["show", "hide"]


def test_fetch_bookmark_page_extracts_work_ids(monkeypatch, tmp_path):
    import app.core.thread_pid_scan as scan

    captured = {}

    def fake_get(url, headers=None, proxies=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["proxies"] = proxies
        return _FakeResponse({
            "body": {
                "total": 2,
                "works": [
                    {"id": "100", "userId": "artist-a"},
                    {"illustId": "101", "userId": "artist-b"},
                ],
            }
        })

    monkeypatch.setattr(scan.requests, "get", fake_get)
    t = _make_thread(tmp_path)

    pids, total, uid_map = t._step2_fetch_bookmark_page(
        "42",
        "cookie",
        "UA",
        "hide",
        48,
        limit=48,
        proxies={"https": "http://proxy"},
    )

    assert pids == ["100", "101"]
    assert total == 2
    assert uid_map == {"100": "artist-a", "101": "artist-b"}
    assert "/ajax/user/42/illusts/bookmarks" in captured["url"]
    assert "rest=hide" in captured["url"]
    assert "offset=48" in captured["url"]
    assert captured["headers"]["Cookie"] == "cookie"


def test_bookmark_run_writes_pictures_id_and_user_ids(monkeypatch, tmp_path):
    t = _make_thread(tmp_path)

    def fake_page(user_id, cookie, agent, rest, offset, limit=48, proxies=None):
        assert user_id == "42"
        assert cookie == "c1"
        pages = {
            ("show", 0): (["100", "101"], 2, {"100": "artist-a", "101": "artist-b"}),
            ("hide", 0): (["200"], 1, {"200": "artist-c"}),
        }
        return pages.get((rest, offset), ([], 0, {}))

    monkeypatch.setattr(t, "_step2_fetch_bookmark_page", fake_page)

    t.run()

    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    assert pics == ["100", "101", "200"]
    assert t._metadata_db.get_artwork("100")["user_id"] == "artist-a"
    assert t._metadata_db.get_artwork("101")["user_id"] == "artist-b"
    assert t._metadata_db.get_artwork("200")["user_id"] == "artist-c"
