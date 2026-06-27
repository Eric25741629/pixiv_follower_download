import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flet as ft
from app.gui.glass import (
    DARK_THEME, LIGHT_THEME, GlassProgressBar, GlassTheme, current_theme,
    glass_panel, glass_pill, glass_nav_item_colors, set_pill_icon,
    set_pill_label, state_colors, style_pill,
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


def test_glass_progress_bar_value_drives_flex_and_visibility():
    bar = GlassProgressBar(color="#112233", bar_height=12, radius=6)
    # 初始：fill 隱藏、track 圓角、模仿 ft.ProgressBar 的屬性面
    assert bar.value == 0
    assert bar.expand is True
    assert bar.bar_height == 12
    assert bar._fill.visible is False
    assert bar.track.border_radius == 6

    bar.value = 0.4
    assert bar._fill.visible is True
    assert bar._fill.expand == 400
    assert bar._rest.expand == 600

    bar.value = 1.0
    assert bar._fill.expand == 1000
    assert bar._rest.visible is False

    bar.value = -3            # clamp 下界
    assert bar.value == 0 and bar._fill.visible is False
    bar.value = 7             # clamp 上界
    assert bar.value == 1.0

    bar.color = "#445566"     # refresh_theme 的就地重染路徑
    assert bar._fill.bgcolor == "#445566"


def test_glass_pill_with_icon_builds_row_with_svg_image():
    p = glass_pill("暫停", DARK_THEME, icon="pause")
    assert isinstance(p.content, ft.Row)
    img, txt = p.content.controls
    assert isinstance(img, ft.Image) and isinstance(txt, ft.Text)
    assert img.data == "pause"
    assert b"<svg" in img.src and b"rect" in img.src
    assert txt.value == "暫停"


def test_set_pill_label_and_icon_swap_in_place():
    p = glass_pill("暫停", DARK_THEME, icon="pause")
    img, txt = p.content.controls
    set_pill_label(p, "繼續")
    set_pill_icon(p, "play")
    assert txt.value == "繼續"
    assert img.data == "play"
    assert b"<path" in img.src           # play 是三角形 path
    # 圖標沿用目前文字前景色
    assert (txt.color or "#FFFFFF").encode() in img.src


def test_set_pill_label_still_works_for_plain_text_pill():
    p = glass_pill("公開", DARK_THEME)
    set_pill_label(p, "全部")
    assert p.content.value == "全部"
    set_pill_icon(p, "play")             # 無圖標膠囊 → no-op 不得丟例外


def test_style_pill_retints_icon_svg_with_new_theme():
    p = glass_pill("停止", DARK_THEME, icon="stop")
    style_pill(p, LIGHT_THEME, primary=True)
    img, txt = p.content.controls
    assert txt.color == LIGHT_THEME.text_primary
    assert LIGHT_THEME.text_primary.encode() in img.src


def test_state_colors_covers_all_states():
    for t in (DARK_THEME, LIGHT_THEME):
        m = state_colors(t)
        assert set(m) == {"idle", "running", "done", "error"}
        for bg, fg in m.values():
            assert bg.startswith("#") and fg.startswith("#")
