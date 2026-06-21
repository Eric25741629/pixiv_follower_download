"""Dialog factories (design system) — wrap ``glass_dialog`` with common shapes."""
from __future__ import annotations

import flet as ft

from app.gui.glass import GlassTheme, glass_dialog


def confirm_dialog(theme: GlassTheme, *, title, content, on_confirm,
                   confirm_text="確定", cancel_text="取消",
                   on_cancel=None) -> ft.AlertDialog:
    """A glass confirm/cancel dialog. ``content`` may be a str or a Control."""
    if isinstance(content, str):
        content = ft.Text(content, color=theme.text_primary)
    actions = [
        ft.TextButton(cancel_text, on_click=on_cancel),
        ft.FilledButton(confirm_text, on_click=on_confirm),
    ]
    return glass_dialog(theme, title, content, actions=actions)
