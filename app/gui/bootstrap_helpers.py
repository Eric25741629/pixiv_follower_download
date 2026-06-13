"""Stateless bootstrap helpers for the Flet ``main(page)`` entry
(file-size refactor).

Genuine module-level functions extracted out of ``flet_app`` — settings/theme
persistence (``_settings_store`` / ``_event_log_kwargs`` / ``_load_theme_mode``
/ ``_save_theme_mode``) and two small view helpers (``_activate_view`` /
``_handle_page_progress_event``). They capture none of ``main``'s locals and
touch no ``flet_app`` module-level mutable state, so moving them is
behavior-preserving. ``flet_app`` re-imports them so existing call sites and
tests (``flet_app._activate_view`` / ``flet_app._handle_page_progress_event``)
keep resolving.

Deliberately NOT moved: ``_emergency_flush`` / ``_install_crash_hooks`` and the
``_PERSISTENT_*`` globals — those are mutated via ``global`` statements inside
``main`` and its closures, so they must stay in ``flet_app``'s module scope.
"""
from __future__ import annotations

import os

import flet as ft

from app.core.app_logging import get_logger
from app.core.settings_store import SettingsStore
from app.gui.views.main_view import MainView

_log = get_logger("pixiv.gui")


def _settings_store() -> SettingsStore:
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return SettingsStore(path)


def _event_log_kwargs() -> dict:
    """Read othersettings.event_log durability/rotation knobs for EventLog.

    Thin wrapper over the Flet-free ``event_log_kwargs_from_settings`` (in
    app.core.event_log) so the GUI and the headless CLI share one reader and the
    CLI never has to import this Flet module just to read these knobs.
    """
    from app.core.event_log import event_log_kwargs_from_settings
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return event_log_kwargs_from_settings(path)


def _load_theme_mode() -> ft.ThemeMode:
    try:
        name = _settings_store().get_section("ui").get("theme_mode", "SYSTEM")
    except Exception:
        return ft.ThemeMode.SYSTEM
    if name == "DARK":
        return ft.ThemeMode.DARK
    if name == "LIGHT":
        return ft.ThemeMode.LIGHT
    return ft.ThemeMode.SYSTEM


def _save_theme_mode(mode: ft.ThemeMode) -> None:
    try:
        _settings_store().update_fields("ui", {"theme_mode": mode.name})
    except Exception:
        _log.exception("failed to persist theme_mode")


def _activate_view(views: list, idx: int) -> int:
    """Show one view while keeping every view mounted in the Flet tree."""
    try:
        active_idx = int(idx)
    except (TypeError, ValueError):
        active_idx = 0
    if active_idx < 0 or active_idx >= len(views):
        active_idx = 0
    for i, view in enumerate(views):
        view.visible = i == active_idx
    return active_idx


def _handle_page_progress_event(main_view: MainView, data) -> None:
    """Normalize page-progress event payloads before touching MainView."""
    if not data:
        main_view.clear_page_progress()
        return
    if isinstance(data, dict):
        delta = data.get("delta", 0)
        total = data.get("total", 0)
        pid = data.get("pid", "")
    elif isinstance(data, tuple):
        if len(data) >= 3:
            delta, total, pid = data[:3]
        elif len(data) == 2:
            delta, total = data
            pid = ""
        else:
            main_view.clear_page_progress()
            return
    else:
        return
    main_view.update_page_progress(delta, total, pid)
