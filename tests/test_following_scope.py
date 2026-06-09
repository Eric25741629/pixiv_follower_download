import sys
from pathlib import Path
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_following import get_following


def test_following_scope_maps_to_pixiv_rest_values(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    t = get_following(Queue(), "42", "cookie", "agent", "all")

    assert t._following_rest_values("public") == ["show"]
    assert t._following_rest_values("private") == ["hide"]
    assert t._following_rest_values("all") == ["show", "hide"]
    assert t._following_rest_values("bad") == ["show", "hide"]


def test_legacy_hidefollow_boolean_maps_to_following_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert get_following(Queue(), "42", "cookie", "agent", True).following_scope == "public"
    assert get_following(Queue(), "42", "cookie", "agent", False).following_scope == "all"


def test_show_hide_rest_names_are_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert get_following(Queue(), "42", "cookie", "agent", "show").following_scope == "public"
    assert get_following(Queue(), "42", "cookie", "agent", "hide").following_scope == "private"
