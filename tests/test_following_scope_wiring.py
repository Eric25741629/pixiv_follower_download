import os
import sys
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import flet as ft

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.settings_store import SettingsStore
from app.gui.run_actions import RunController
from app.gui.views.main_view import MainView
from app.gui.views.settings_view import SettingsView


def _fake_page():
    return SimpleNamespace(
        theme_mode=ft.ThemeMode.LIGHT,
        platform_brightness=ft.Brightness.LIGHT,
        services=[],
    )


def _settings_base(tmp_path):
    return Path(os.environ["APPDATA"]) / "pixiv_download"


def test_build_step1_passes_following_scope_to_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    captured = {}

    rc = RunController.__new__(RunController)
    rc._event_q = Queue()
    monkeypatch.setattr(rc, "_validate_cookies_for_step", lambda *args: ["cookie"])

    def fake_get_following(q, userid, cookie, agent, following_scope):
        captured.update(
            {
                "q": q,
                "userid": userid,
                "cookie": cookie,
                "agent": agent,
                "following_scope": following_scope,
            }
        )
        return object()

    from app.core import thread_following

    monkeypatch.setattr(thread_following, "get_following", fake_get_following)

    rc._build_step1(
        {"userid": "42"},
        "agent",
        {"following_scope": "private"},
        {"hidefollow": False},
    )

    assert captured == {
        "q": rc._event_q,
        "userid": "42",
        "cookie": "cookie",
        "agent": "agent",
        "following_scope": "private",
    }


def test_main_view_following_scope_button_persists_choice(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    view = MainView(_fake_page(), Queue())

    view._on_following_scope_change("private")

    dl = SettingsStore(str(_settings_base(tmp_path))).get_section("download")
    assert dl["following_scope"] == "private"
    assert view._following_scope == "private"


def test_settings_view_saves_following_scope_dropdown(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    base = _settings_base(tmp_path)
    SettingsStore(str(base)).update_fields("download", {"following_scope": "private"})

    view = SettingsView(_fake_page())

    assert view._dd_following_scope.value == "private"
    assert [opt.key for opt in view._dd_following_scope.options] == [
        "public",
        "private",
        "all",
    ]

    view._dd_following_scope.value = "public"
    view.save()

    dl = SettingsStore(str(base)).get_section("download")
    assert dl["following_scope"] == "public"
