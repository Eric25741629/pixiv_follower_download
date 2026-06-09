from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.settings_store import SettingsStore


def test_live_settings_sections_cache_until_settings_signature_changes(tmp_path, monkeypatch):
    from app.core.live_settings import LiveSettings

    store = SettingsStore(str(tmp_path))
    store.save({"download": {"like_num": 1}})

    calls = []
    original_load = SettingsStore.load

    def spy_load(self):
        calls.append(1)
        return original_load(self)

    monkeypatch.setattr(SettingsStore, "load", spy_load)

    live = LiveSettings(str(tmp_path))
    first = live.sections()
    second = live.sections()

    assert first["download"]["like_num"] == 1
    assert second["download"]["like_num"] == 1
    assert len(calls) == 1

    store.save({"download": {"like_num": 12345}})

    third = live.sections()
    assert third["download"]["like_num"] == 12345
    assert len(calls) == 2


def test_live_settings_returns_only_live_relevant_sections(tmp_path):
    from app.core.live_settings import LiveSettings

    SettingsStore(str(tmp_path)).save({
        "download": {"like_num": 7},
        "auth": {"cookies": "secret"},
        "performance": {"pid_cooldown_avg": 44},
    })

    sections = LiveSettings(str(tmp_path)).sections()

    assert set(sections) == {"download", "filter", "directory", "performance", "jxl"}
    assert sections["download"]["like_num"] == 7
    assert sections["performance"]["pid_cooldown_avg"] == 44
    assert "auth" not in sections
