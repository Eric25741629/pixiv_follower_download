import contextlib
import io
import json as _json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.settings_store import DEFAULTS, SettingsStore


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


def test_config_set_then_get_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.cli import commands
    assert commands.main(["config", "set", "download.like_num", "250"]) == 0
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert commands.main(["config", "get", "download.like_num", "--json"]) == 0
    assert _json.loads(out.getvalue())["value"] == 250  # int type inferred


def test_config_set_bool_inference(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.cli import commands
    assert commands.main(["config", "set", "download.combined_mode", "true"]) == 0
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        commands.main(["config", "get", "download.combined_mode", "--json"])
    assert _json.loads(out.getvalue())["value"] is True
