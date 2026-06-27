"""Toggling a settings Switch must persist immediately (主動紀錄), without
requiring the user to click 「儲存設定」.

Root cause this guards against: the new combined_mode / author_order switches
(and every other switch) only persisted via the explicit save button, so a
user who flipped them and ran the workflow got the default (off) — combined
mode never merged Step 3+4, and the toggle reverted next session.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest


class _FakeServices:
    def extend(self, items):
        pass


class _FakePage:
    """Minimal stand-in: SettingsView.__init__ only calls page.services.extend."""

    def __init__(self):
        self.services = _FakeServices()


@pytest.fixture
def view(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.gui.views.settings_view import SettingsView

    return SettingsView(_FakePage())


def _section(section: str) -> dict:
    from app.core.settings_store import SettingsStore

    base = os.getenv("APPDATA") + r"/pixiv_download/"
    return SettingsStore(base).get_section(section)


def _fire(switch, value) -> None:
    """Simulate a user flipping *switch* to *value* and Flet firing on_change."""
    switch.value = value
    assert callable(switch.on_change), "switch.on_change is not wired for auto-save"
    switch.on_change(None)


def test_toggle_combined_mode_autosaves(view):
    _fire(view._sw_combined_mode, True)
    assert _section("download").get("combined_mode") is True


def test_toggle_author_order_autosaves(view):
    _fire(view._sw_author_order, True)
    assert _section("download").get("author_order") is True


def test_autosave_does_not_need_save_button(view):
    # nogif lives in the 'filter' section; toggling it must persist on its own.
    _fire(view._sw_nogif, True)
    assert _section("filter").get("nogif") is True


def test_toggle_r18_dir_inverted_autosaves(view):
    # Switch ON => "create R-18 dir" => stored no_R18_dir == False (inverted).
    _fire(view._sw_r18_dir, True)
    assert _section("directory").get("no_R18_dir") is False
    _fire(view._sw_r18_dir, False)
    assert _section("directory").get("no_R18_dir") is True


def test_autosave_one_field_does_not_clobber_other_unsaved_edits(view):
    # Auto-save must write only its own field, leaving sibling fields intact.
    _fire(view._sw_combined_mode, True)
    _fire(view._sw_author_order, True)
    dl = _section("download")
    assert dl.get("combined_mode") is True
    assert dl.get("author_order") is True
