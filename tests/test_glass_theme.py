import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flet as ft
from app.gui.glass import (
    DARK_THEME, LIGHT_THEME, GlassTheme, current_theme,
    glass_panel, glass_pill, glass_nav_item_colors, state_colors,
)


class _FakePage:
    def __init__(self, mode):
        self.theme_mode = mode
        self.platform_brightness = ft.Brightness.DARK


def test_theme_tokens_complete():
    for t in (DARK_THEME, LIGHT_THEME):
        assert t.panel_bg.startswith("#") and len(t.panel_bg) == 9  # AARRGGBB
        assert t.accent and t.text_primary and t.success
        assert len(t.bg_gradient) == 3
        assert t.panel_blur > 0


def test_current_theme_switches():
    assert current_theme(_FakePage(ft.ThemeMode.DARK)) is DARK_THEME
    assert current_theme(_FakePage(ft.ThemeMode.LIGHT)) is LIGHT_THEME
    assert current_theme(_FakePage(ft.ThemeMode.SYSTEM)) is DARK_THEME  # brightness=DARK


def test_glass_panel_is_tinted_blurred_container():
    p = glass_panel(ft.Text("x"), DARK_THEME)
    assert isinstance(p, ft.Container)
    assert p.bgcolor == DARK_THEME.panel_bg
    assert p.blur is not None
    assert p.border_radius == DARK_THEME.radius


def test_glass_panel_blur_disabled_fallback():
    t = GlassTheme(**{**DARK_THEME.__dict__, "blur_enabled": False})
    p = glass_panel(ft.Text("x"), t)
    assert p.blur is None
    assert p.bgcolor == t.panel_bg_opaque  # 降級為不透明


def test_state_colors_covers_all_states():
    for t in (DARK_THEME, LIGHT_THEME):
        m = state_colors(t)
        assert set(m) == {"idle", "running", "done", "error"}
        for bg, fg in m.values():
            assert bg.startswith("#") and fg.startswith("#")
