"""Button / action-control factories (design system).

Thin wrappers so the app's primary/secondary/icon actions are produced in one
place and can be restyled (e.g. swapped to glass pills) without touching views.
"""
from __future__ import annotations

import flet as ft


def primary_button(text: str, *, icon=None, on_click=None) -> ft.FilledButton:
    return ft.FilledButton(text, icon=icon, on_click=on_click)


def secondary_button(text: str, *, icon=None, on_click=None) -> ft.OutlinedButton:
    return ft.OutlinedButton(text, icon=icon, on_click=on_click)


def icon_action(icon, *, on_click=None, tooltip=None) -> ft.IconButton:
    return ft.IconButton(icon=icon, on_click=on_click, tooltip=tooltip)
