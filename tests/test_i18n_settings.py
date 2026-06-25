import json
import os

from app.core.settings_store import DEFAULTS, SettingsStore


def test_defaults_have_ui_language():
    assert DEFAULTS["ui"]["language"] == "zh-TW"


def test_existing_settings_without_language_get_default(tmp_path):
    # Simulate an old settings.json that predates ui.language.
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"ui": {"theme_mode": "DARK"}}), encoding="utf-8")
    store = SettingsStore(str(tmp_path) + os.sep)
    ui = store.get_section("ui")
    assert ui["theme_mode"] == "DARK"      # preserved
    assert ui["language"] == "zh-TW"        # back-filled from DEFAULTS
