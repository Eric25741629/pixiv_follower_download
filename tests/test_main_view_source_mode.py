"""MainView source-mode band: following vs bookmark collection."""
import os
import sys
from queue import Queue

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import app.i18n as i18n


def setup_function(_fn):
    # Scope labels / step-card labels resolve via i18n.t(); pin zh-TW so the
    # zh assertions below are stable regardless of test ordering.
    i18n.set_locale(i18n.BASE_LOCALE)


class _FakePage:
    pass


@pytest.fixture
def view(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.gui.views.main_view import MainView

    return MainView(_FakePage(), Queue())


def test_refresh_source_mode_applies_bookmark_labels_and_scope(view, tmp_path):
    from app.core.settings_store import SettingsStore

    base = os.getenv("APPDATA") + r"/pixiv_download/"
    SettingsStore(base).update_fields(
        "download",
        {"source_mode": "bookmarks", "bookmark_scope": "private"},
    )

    view.refresh_source_mode()

    assert view._source_mode == "bookmarks"
    assert view._active_scope == "private"
    # 抓收藏 pill 為 active（accent_fill 底）；抓追隨為非 active。
    from app.gui.glass import current_theme
    theme = current_theme(view._page)
    assert view._btn_source_bookmarks.bgcolor == theme.accent_fill
    assert view._btn_source_following.bgcolor != theme.accent_fill
    assert view._scope_row.visible is True
    assert view._scope_label.value == "收藏範圍"
    assert view._step_card_texts[0].value == "步驟 1\n略過追隨"
    assert view._step_card_texts[1].value == "步驟 2\n抓收藏 PID"


def test_apply_source_mode_restores_following_labels(view):
    from app.gui.views.main_view import step_labels

    view.apply_source_mode("bookmarks", "private")
    view.apply_source_mode("following", "all")

    assert view._source_mode == "following"
    assert view._active_scope == "all"
    from app.gui.glass import current_theme
    theme = current_theme(view._page)
    assert view._btn_source_following.bgcolor == theme.accent_fill
    assert view._scope_row.visible is True
    assert view._scope_label.value == "追隨範圍"
    assert view._step_card_texts[0].value == step_labels()[0]
    assert view._step_card_texts[1].value == step_labels()[1]


def test_source_mode_buttons_live_in_wrapping_mode_row(view):
    view.apply_source_mode("following", "all")

    # 模式列 = [來源群組, 範圍群組]，wrap=True 讓縮窗時整組換行不重疊。
    assert view._mode_row.wrap is True
    assert view._mode_row.controls == [view._source_group, view._scope_row]
    # 群組必須 tight=True（MainAxisSize.min）：非 tight 的巢狀 Row 在 Wrap 裡
    # 會撐滿整行，導致來源與範圍永遠各佔一列。
    assert view._source_group.tight is True
    assert view._scope_row.tight is True
    assert view._source_mode_controls.tight is True
    assert view._source_mode_controls.controls == [
        view._btn_source_following,
        view._btn_source_bookmarks,
    ]


def test_source_mode_button_persists_bookmark_choice(view):
    from app.core.settings_store import SettingsStore

    view._on_source_mode_change("bookmarks")

    base = os.getenv("APPDATA") + r"/pixiv_download/"
    dl = SettingsStore(base).get_section("download")
    assert dl["source_mode"] == "bookmarks"
    assert view._source_mode == "bookmarks"


def test_following_scope_button_persists_following_choice(view):
    from app.core.settings_store import SettingsStore

    view.apply_source_mode("following", "all")
    view._on_scope_change("private")

    base = os.getenv("APPDATA") + r"/pixiv_download/"
    dl = SettingsStore(base).get_section("download")
    assert dl["following_scope"] == "private"
    assert dl["bookmark_scope"] == "all"
    assert view._active_scope == "private"


def test_bookmark_scope_button_still_persists_bookmark_choice(view):
    from app.core.settings_store import SettingsStore

    view.apply_source_mode("bookmarks", "all")
    view._on_scope_change("public")

    base = os.getenv("APPDATA") + r"/pixiv_download/"
    dl = SettingsStore(base).get_section("download")
    assert dl["bookmark_scope"] == "public"
    assert dl["following_scope"] == "all"
    assert view._active_scope == "public"
