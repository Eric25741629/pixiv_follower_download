import os, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.settings_store import SettingsStore, DEFAULTS


def test_schedule_section_default_shape():
    assert "schedule" in DEFAULTS
    s = DEFAULTS["schedule"]
    assert s["enabled"] is False
    assert s["mode"] in ("daily", "interval")
    assert s["time"] == "03:00"
    assert int(s["interval_hours"]) == 6
    assert s["action"] == "run_all"


def test_settingsstore_returns_schedule_section():
    with tempfile.TemporaryDirectory() as d:
        store = SettingsStore(d)
        sec = store.get_section("schedule")
        assert sec["enabled"] is False
        assert sec["mode"] == "daily"
