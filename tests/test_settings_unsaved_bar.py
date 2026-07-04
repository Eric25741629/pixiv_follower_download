"""Floating unsaved-changes bar + editable/pasteable path fields.

The bottom save button became a bottom-floating bar that only appears once a
save-requiring control changes. Switches / language dropdown keep autosaving, so
they never trip the dirty flag. Path fields dropped read_only so users can type
or paste (with a light quote/whitespace normalization on save).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import flet as ft

import app.i18n as i18n
from app.core.settings_store import SettingsStore
from app.gui.views.settings_view import SettingsView


def setup_function(_fn):
    i18n.set_locale(i18n.BASE_LOCALE)


class _FakeServices:
    def extend(self, items):
        pass


class _FakePage:
    def __init__(self):
        self.services = _FakeServices()


def _view(tmp_path, monkeypatch) -> SettingsView:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return SettingsView(_FakePage())


def _download_section(tmp_path) -> dict:
    base = os.getenv("APPDATA") + r"/pixiv_download/"
    return SettingsStore(base).get_section("download")


def _walk(ctl):
    yield ctl
    for attr in ("controls", "actions"):
        for child in getattr(ctl, attr, None) or []:
            yield from _walk(child)
    content = getattr(ctl, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)


def test_path_fields_are_editable(tmp_path, monkeypatch):
    view = _view(tmp_path, monkeypatch)
    assert not view._tf_path.read_only
    assert not view._tf_jxl_path.read_only


def test_editing_a_field_marks_dirty_and_shows_bar(tmp_path, monkeypatch):
    view = _view(tmp_path, monkeypatch)
    assert view._dirty is False
    assert view._unsaved_bar.visible is False

    view._tf_account.on_change(None)

    assert view._dirty is True
    assert view._unsaved_bar.visible is True


def test_save_clears_dirty_and_hides_bar(tmp_path, monkeypatch):
    view = _view(tmp_path, monkeypatch)
    view._tf_account.on_change(None)
    assert view._dirty is True

    view.save()

    assert view._dirty is False
    assert view._unsaved_bar.visible is False


def test_switch_autosave_does_not_mark_dirty(tmp_path, monkeypatch):
    view = _view(tmp_path, monkeypatch)
    view._sw_nogif.value = True
    view._sw_nogif.on_change(None)  # autosave handler, not _mark_dirty
    assert view._dirty is False
    assert view._unsaved_bar.visible is False


def test_build_root_is_stack_with_bar_and_no_column_save_button(tmp_path, monkeypatch):
    view = _view(tmp_path, monkeypatch)
    root = view.build()

    assert isinstance(root, ft.Stack)
    assert view._unsaved_bar in root.controls

    column = root.controls[0]
    # The only FilledButton (save) now lives in the floating bar, not the Column.
    assert not any(isinstance(x, ft.FilledButton) for x in _walk(column))
    assert any(isinstance(x, ft.FilledButton) for x in _walk(view._unsaved_bar))


def test_save_normalizes_pasted_quoted_path(tmp_path, monkeypatch):
    view = _view(tmp_path, monkeypatch)
    view._tf_path.value = ' "C:/some/dir" '
    view.save()

    assert _download_section(tmp_path)["path"] == "C:/some/dir"


def test_source_dropdown_marks_dirty_language_dropdown_does_not(tmp_path, monkeypatch):
    view = _view(tmp_path, monkeypatch)
    assert view._dd_source_mode.on_select is not None
    assert view._dd_source_mode.on_select.__func__ is SettingsView._mark_dirty
    # Language dropdown keeps its own apply-on-restart handler, not _mark_dirty.
    assert view._dd_language.on_select.__func__ is SettingsView._on_language_select
