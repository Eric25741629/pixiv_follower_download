"""RunController and Step 2 wiring for bookmark PID source."""
import os
import sys
from queue import Queue

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def test_run_all_starts_at_step2_when_bookmark_source(monkeypatch, tmp_path):
    import app.gui.run_actions as ra

    class _FakeStore:
        def migrate_from_legacy(self):
            pass

        def get_section(self, name):
            return {
                "download": {"source_mode": "bookmarks"},
            }.get(name, {})

    started = []
    monkeypatch.setattr(ra, "_store", lambda: _FakeStore())
    monkeypatch.setattr(ra.RunController, "_backup_db", lambda self: None)
    monkeypatch.setattr(ra.RunController, "_start_step", lambda self, n: started.append(n))

    rc = ra.RunController(main_view=object(), event_q=Queue())
    rc.run_all()

    assert started == [2]


def test_build_step2_bookmark_source_does_not_require_following(monkeypatch, tmp_path):
    import app.gui.run_actions as ra

    class _FakeStore:
        def migrate_from_legacy(self):
            pass

        def get_section(self, name):
            return {
                "auth": {"userid": "42", "cookies": "c1"},
                "download": {
                    "source_mode": "bookmarks",
                    "bookmark_scope": "private",
                    "path": str(tmp_path),
                },
                "filter": {},
                "performance": {},
                "directory": {},
                "jxl": {},
            }[name]

    monkeypatch.setattr(ra, "_store", lambda: _FakeStore())
    monkeypatch.setattr(ra, "_data_path", lambda: str(tmp_path))
    monkeypatch.setattr(ra, "_load_author_list", lambda: [])
    monkeypatch.setattr(
        ra.RunController,
        "_validate_cookies_for_step",
        lambda self, a, ag, n: ["c1"],
    )
    monkeypatch.setattr(ra.RunController, "_build_scheduler", lambda self, *a, **k: None)

    rc = ra.RunController(main_view=object(), event_q=Queue())
    t = rc._build_thread(2)

    assert t is not None
    assert t.source_mode == "bookmarks"
    assert t.bookmark_scope == "private"
    assert t.bookmark_user_id == "42"
    assert t.Author_list == []


def test_build_step1_passes_following_scope_to_worker(monkeypatch, tmp_path):
    import app.gui.run_actions as ra

    class _FakeStore:
        def migrate_from_legacy(self):
            pass

        def get_section(self, name):
            return {
                "auth": {"userid": "42", "cookies": "c1"},
                "download": {
                    "source_mode": "following",
                    "following_scope": "private",
                    "path": str(tmp_path),
                },
                "filter": {"hidefollow": True},
                "performance": {},
                "directory": {},
                "jxl": {},
            }[name]

    captured = {}

    def fake_get_following(q, userid, cookie, agent, following_scope):
        captured.update(
            {
                "userid": userid,
                "cookie": cookie,
                "following_scope": following_scope,
            }
        )
        return object()

    monkeypatch.setattr(ra, "_store", lambda: _FakeStore())
    monkeypatch.setattr(ra, "_data_path", lambda: str(tmp_path))
    monkeypatch.setattr(
        ra.RunController,
        "_validate_cookies_for_step",
        lambda self, a, ag, n: ["c1"],
    )
    monkeypatch.setattr(ra.thread_following, "get_following", fake_get_following)

    rc = ra.RunController(main_view=object(), event_q=Queue())
    t = rc._build_thread(1)

    assert t is not None
    assert captured == {
        "userid": "42",
        "cookie": "c1",
        "following_scope": "private",
    }
