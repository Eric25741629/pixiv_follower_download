"""Glass-themed input control factories (design system).

Each factory returns a standard Flet input control pre-styled with GlassTheme
tokens — the border/focus/active/inactive recipe that views used to retrofit in
bulk via ``_apply_input_theme``. Centralising it here means a control is born
styled and there is one place to change input theming. Built ON ``glass.py``.
"""
from __future__ import annotations

import flet as ft

from app.gui.glass import GlassTheme


def _style_text_input(ctl: ft.Control, theme: GlassTheme) -> ft.Control:
    ctl.border_color = theme.panel_border
    ctl.focused_border_color = theme.accent
    return ctl


def number_field(theme: GlassTheme, *, label, value, width=150,
                 tooltip=None, on_change=None) -> ft.TextField:
    return _style_text_input(ft.TextField(
        label=label, value=str(value), width=width,
        keyboard_type=ft.KeyboardType.NUMBER, tooltip=tooltip, on_change=on_change,
    ), theme)


def text_field(theme: GlassTheme, *, label, value="", width=None, password=False,
               hint_text=None, read_only=False, expand=False,
               tooltip=None, on_change=None) -> ft.TextField:
    kw = dict(
        label=label, value=str(value), password=password,
        can_reveal_password=password, hint_text=hint_text, read_only=read_only,
        expand=expand, tooltip=tooltip, on_change=on_change,
    )
    if width is not None:
        kw["width"] = width
    return _style_text_input(ft.TextField(**kw), theme)


def multiline_field(theme: GlassTheme, *, label, value="", min_lines=4,
                    max_lines=15, hint_text=None, expand=True) -> ft.TextField:
    return _style_text_input(ft.TextField(
        label=label, value=str(value), multiline=True, min_lines=min_lines,
        max_lines=max_lines, hint_text=hint_text, expand=expand,
    ), theme)


def dropdown(theme: GlassTheme, *, label, value, options, width=220,
             on_select=None, text_size=None, content_padding=None) -> ft.Dropdown:
    """``options`` is a list of ``(key, label)`` tuples.

    Flet 0.84's Dropdown change handler is ``on_select`` (it has NO ``on_change``
    attribute — setting one is a silent no-op), so callers pass ``on_select``.
    """
    opts = [ft.dropdown.Option(key, text) for (key, text) in options]
    ctl = ft.Dropdown(label=label, value=str(value), options=opts, width=width)
    if on_select is not None:
        ctl.on_select = on_select
    if text_size is not None:
        ctl.text_size = text_size
    if content_padding is not None:
        ctl.content_padding = content_padding
    ctl.border_color = theme.panel_border
    ctl.focused_border_color = theme.accent
    return ctl


def switch(theme: GlassTheme, *, label, value, tooltip=None,
           on_change=None) -> ft.Switch:
    return ft.Switch(label=label, value=bool(value), tooltip=tooltip,
                     on_change=on_change, active_color=theme.accent)


def slider(theme: GlassTheme, *, min, max, divisions, value, width,
           label="{value}", on_change=None) -> ft.Slider:
    # inactive_color=panel_border so the track is visible on the light glass bg
    # (the M3 default is near-invisible there).
    return ft.Slider(min=min, max=max, divisions=divisions, value=float(value),
                     label=label, width=width, on_change=on_change,
                     active_color=theme.accent, inactive_color=theme.panel_border)
