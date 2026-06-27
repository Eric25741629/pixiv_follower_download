import os

from app.core.settings_store import SettingsStore
from app.gui.views.settings_view import SettingsView


def test_persist_language_writes_ui_language_preserving_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    base = os.path.join(str(tmp_path), "pixiv_download") + os.sep
    os.makedirs(base, exist_ok=True)
    SettingsStore(base).update_fields("ui", {"theme_mode": "DARK"})

    view = object.__new__(SettingsView)   # bypass __init__ (no ft.Page needed)
    view._persist_language("en")

    ui = SettingsStore(base).get_section("ui")
    assert ui["language"] == "en"
    assert ui["theme_mode"] == "DARK"      # untouched
