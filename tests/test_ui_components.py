"""Design-system component factories produce correctly-typed, glass-styled Flet
controls. These are the contract the view migration relies on."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import flet as ft
from app.gui.glass import DARK_THEME as T
from app.gui import components as c


# ── inputs ──────────────────────────────────────────────────────────────────
def test_number_field():
    f = c.number_field(T, label="讚數", value=10, width=150)
    assert isinstance(f, ft.TextField)
    assert f.label == "讚數" and f.value == "10" and f.width == 150
    assert f.keyboard_type == ft.KeyboardType.NUMBER
    assert f.border_color == T.panel_border
    assert f.focused_border_color == T.accent


def test_text_field_password():
    f = c.text_field(T, label="密碼", value="x", width=300, password=True)
    assert isinstance(f, ft.TextField)
    assert f.password is True and f.can_reveal_password is True
    assert f.width == 300 and f.border_color == T.panel_border


def test_text_field_expand_no_width():
    f = c.text_field(T, label="路徑", value="", read_only=True, expand=True)
    assert f.expand is True and f.read_only is True
    assert f.width is None  # expand fields take no fixed width


def test_multiline_field():
    f = c.multiline_field(T, label="proxy", value="a\nb", min_lines=4, max_lines=15)
    assert f.multiline is True and f.min_lines == 4 and f.max_lines == 15
    assert f.focused_border_color == T.accent


def test_dropdown_builds_options_from_tuples():
    handler = lambda e: None
    d = c.dropdown(T, label="來源", value="following",
                   options=[("following", "追隨"), ("bookmarks", "收藏")], width=220,
                   on_select=handler, text_size=11)
    assert isinstance(d, ft.Dropdown)
    assert d.value == "following" and d.width == 220
    assert [o.key for o in d.options] == ["following", "bookmarks"]
    assert d.border_color == T.panel_border and d.focused_border_color == T.accent
    # Flet 0.84 Dropdown's change handler is on_select, NOT on_change.
    assert d.on_select is handler
    assert d.text_size == 11


def test_switch():
    s = c.switch(T, label="啟用", value=True, tooltip="hi")
    assert isinstance(s, ft.Switch)
    assert s.value is True and s.tooltip == "hi" and s.active_color == T.accent


def test_slider_visibility_fix():
    s = c.slider(T, min=0, max=300, divisions=60, value=35, width=240)
    assert isinstance(s, ft.Slider)
    assert s.active_color == T.accent
    assert s.inactive_color == T.panel_border  # the track-visibility fix


# ── layout ──────────────────────────────────────────────────────────────────
def test_subhead_and_note():
    h = c.subhead(T, "標題")
    assert h.size == 12 and h.weight == ft.FontWeight.BOLD and h.color == T.text_primary
    n = c.note(T, "說明")
    assert n.size == 11 and n.color == T.text_secondary


def test_status_note_kinds():
    assert c.status_note(T, "ok", "success").color == T.success
    assert c.status_note(T, "bad", "error").color == T.error
    assert c.status_note(T, "warn", "warning").color == T.warning
    assert c.status_note(T, "plain").color == T.text_secondary


def test_page_title_and_inline_label():
    assert c.page_title(T, "設定").size == 20
    assert c.inline_label(T, "x").size == 13
    assert c.inline_label(T, "x", size=12).size == 12


def test_section_returns_control_wrapping_expansion_tile():
    sec = c.section(T, "帳號", [ft.Text("a"), ft.Text("b")])
    assert isinstance(sec, ft.Control)


def test_unsaved_bar_hidden_by_default_with_button_and_bottom_position():
    btn = c.primary_button("儲存設定", icon=ft.Icons.SAVE)
    bar = c.unsaved_bar(T, text="有設定尚未儲存", save_button=btn)
    assert isinstance(bar, ft.Container)
    # Starts hidden; the owning view flips .visible when dirty.
    assert bar.visible is False
    # Stack-positioned along the bottom edge, full width.
    assert bar.bottom == 16 and bar.left == 0 and bar.right == 0
    # Message (left) + the passed save button (right) inside the glass panel Row.
    row = bar.content.content
    assert isinstance(row, ft.Row)
    assert row.alignment == ft.MainAxisAlignment.SPACE_BETWEEN
    assert btn in row.controls
    assert any(isinstance(x, ft.Text) and x.value == "有設定尚未儲存" for x in row.controls)


# ── buttons / dialogs ───────────────────────────────────────────────────────
def test_buttons():
    assert isinstance(c.primary_button("存", icon=ft.Icons.SAVE), ft.FilledButton)
    assert isinstance(c.secondary_button("測試"), ft.OutlinedButton)
    assert isinstance(c.icon_action(ft.Icons.ADD), ft.IconButton)


def test_confirm_dialog():
    d = c.confirm_dialog(T, title="確認", content="確定嗎", on_confirm=lambda e: None)
    assert isinstance(d, ft.AlertDialog)
    assert len(d.actions) == 2
    assert isinstance(d.actions[0], ft.TextButton)
    assert isinstance(d.actions[1], ft.FilledButton)
