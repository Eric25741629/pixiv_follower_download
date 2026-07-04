"""App design system: reusable, glass-themed Flet component factories.

ALL new UI should be built from these factories instead of hand-rolling
``ft.*`` controls in views, so styling and behavior stay consistent and live in
one place. Built on top of ``app.gui.glass`` (GlassTheme tokens + glass_panel /
glass_dialog). The enforcement test ``tests/test_ui_no_raw_controls.py`` fails
if a view instantiates a raw themed control outside this package.

Layers:
- inputs:  number_field, text_field, multiline_field, dropdown, switch, slider
- layout:  page_title, subhead, note, status_note, inline_label, section
- buttons: primary_button, secondary_button, icon_action
- dialogs: confirm_dialog
"""
from app.gui.components.inputs import (
    number_field, text_field, multiline_field, dropdown, switch, slider,
)
from app.gui.components.layout import (
    page_title, subhead, note, status_note, inline_label, section, unsaved_bar,
)
from app.gui.components.buttons import (
    primary_button, secondary_button, icon_action,
)
from app.gui.components.dialogs import confirm_dialog

__all__ = [
    "number_field", "text_field", "multiline_field", "dropdown", "switch", "slider",
    "page_title", "subhead", "note", "status_note", "inline_label", "section", "unsaved_bar",
    "primary_button", "secondary_button", "icon_action",
    "confirm_dialog",
]
