"""Glass-themed layout / typography factories (design system).

Promotes the per-view inline lambdas (``subhead``, ``note``) and the duplicated
``_tile`` helper to one shared, theme-aware place. Built ON ``glass.py``.
"""
from __future__ import annotations

import flet as ft

from app.gui.glass import GlassTheme, glass_panel


def page_title(theme: GlassTheme, text: str) -> ft.Text:
    return ft.Text(text, size=20, weight=ft.FontWeight.BOLD, color=theme.text_primary)


def subhead(theme: GlassTheme, text: str) -> ft.Text:
    return ft.Text(text, size=12, weight=ft.FontWeight.BOLD, color=theme.text_primary)


def note(theme: GlassTheme, text: str) -> ft.Text:
    return ft.Text(text, size=11, color=theme.text_secondary)


def status_note(theme: GlassTheme, text: str, kind: str | None = None) -> ft.Text:
    """A size-11 caption coloured by state. ``kind`` in
    {success, error, warning, info} or None (secondary)."""
    color = {
        "success": theme.success,
        "error": theme.error,
        "warning": theme.warning,
        "info": theme.info,
    }.get(kind, theme.text_secondary)
    return ft.Text(text, size=11, color=color)


def inline_label(theme: GlassTheme, text: str, size: int = 13) -> ft.Text:
    return ft.Text(text, size=size, color=theme.text_primary)


def unsaved_bar(theme: GlassTheme, *, text: str, save_button: ft.Control) -> ft.Container:
    """Floating unsaved-changes banner (message left, save button right).

    Returned hidden (``visible=False``) and Stack-positioned along the bottom
    edge; the owning view flips ``.visible`` when settings go dirty/clean.
    Horizontal padding keeps the glass panel off the window edges.
    """
    row = ft.Row(
        [ft.Text(text, color=theme.text_primary), save_button],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )
    return ft.Container(
        content=glass_panel(row, theme),
        visible=False,
        bottom=16,
        left=0,
        right=0,
        padding=ft.Padding.symmetric(horizontal=24),
    )


def section(theme: GlassTheme, title: str, controls: list) -> ft.Control:
    """Collapsible glass section card — the unified ``_tile``."""
    tile = ft.ExpansionTile(
        title=ft.Text(title, color=theme.text_primary),
        controls=[ft.Container(
            content=ft.Column(controls, spacing=8),
            padding=ft.Padding.only(left=16, top=10, bottom=12),
        )],
        bgcolor="#00000000",
        collapsed_bgcolor="#00000000",
        text_color=theme.text_primary,
        collapsed_text_color=theme.text_primary,
        icon_color=theme.accent,
        collapsed_icon_color=theme.text_secondary,
        shape=ft.RoundedRectangleBorder(radius=theme.radius),
        collapsed_shape=ft.RoundedRectangleBorder(radius=theme.radius),
    )
    return glass_panel(tile, theme, padding=ft.Padding(4, 4, 4, 4))
