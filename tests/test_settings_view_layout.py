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
        title = getattr(control, "title", None)
        value = getattr(title, "value", None)
        if value:
            titles.append(value)
    return titles


def test_settings_view_uses_five_major_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.gui.views.settings_view import SettingsView

    view = SettingsView(_FakePage())

    titles = _tile_titles(view.build())

    assert titles == [
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
