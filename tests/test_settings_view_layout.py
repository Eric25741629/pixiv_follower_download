import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class _FakeServices:
    def extend(self, items):
        pass


class _FakePage:
    def __init__(self):
        self.services = _FakeServices()


def _tile_titles(column):
    titles = []
    for control in getattr(column, "controls", []):
        # Liquid-glass migration: each ExpansionTile is wrapped in a glass
        # Container — unwrap one level of .content before reading .title.
        tile = getattr(control, "content", None) or control
        title = getattr(tile, "title", None)
        value = getattr(title, "value", None)
        if value:
            titles.append(value)
    return titles


def test_settings_view_major_section_titles(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.gui.views.settings_view import SettingsView

    view = SettingsView(_FakePage())

    titles = _tile_titles(view.build())

    # The i18n "介面" (language) row leads; the content sections follow.
    # (The spec's 8-section restructure is a separate follow-on step.)
    assert titles == [
        "介面",
        "帳號與連線",
        "下載輸出",
        "作品篩選",
        "執行流程",
        "速度與自動化",
    ]


def test_ai_directory_switch_label_makes_disable_behavior_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.gui.views.settings_view import SettingsView

    view = SettingsView(_FakePage())

    assert "關閉後下載到一般路徑" in view._sw_ai_dir.label


def test_settings_view_exposes_download_source_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.gui.views.settings_view import SettingsView

    view = SettingsView(_FakePage())

    assert view._dd_source_mode.value == "following"
    assert view._dd_following_scope.value == "all"
    assert view._dd_bookmark_scope.value == "all"
    assert [opt.key for opt in view._dd_source_mode.options] == ["following", "bookmarks"]
    assert [opt.key for opt in view._dd_following_scope.options] == ["public", "private", "all"]
    assert [opt.key for opt in view._dd_bookmark_scope.options] == ["public", "private", "all"]
