"""Liquid-glass design system — the ONLY source of visual style for app/gui.

色值定案來源：docs/specs/2026-06-11-liquid-glass-ui-design.md。
所有 view 一律從本模組取得顏色與元件，禁止在 view 內硬編碼顏色。
"""
from __future__ import annotations

from dataclasses import dataclass

import flet as ft

GITHUB_URL = "https://github.com/Eric25741629/pixiv_follower_download"

# 全域字型：繁中 UI 在 Windows 上用微軟正黑體 UI，比 Flutter 預設的 CJK
# fallback 粗實清晰。由 flet_app 接到 page.theme(font_family=FONT_FAMILY)。
FONT_FAMILY = "Microsoft JhengHei UI"


@dataclass(frozen=True)
class GlassTheme:
    name: str
    # 背景
    bg_gradient: list[str]          # 3-stop linear gradient（160deg 近似：begin top_left → end bottom_right）
    orb1_color: str                 # 右上光暈（含 alpha 的 #AARRGGBB）
    orb2_color: str                 # 左下光暈
    # 玻璃面板（深色=染色玻璃，淺色=霜玻璃）
    panel_bg: str                   # 半透明 fill
    panel_bg_opaque: str            # blur_enabled=False 時的降級色
    panel_blur: int
    panel_border: str
    panel_highlight: str            # 內側上緣高光（BoxShadow inset 不支援 → 用 border 上緣近似）
    panel_shadow: str
    # 色彩
    accent: str
    accent_fill: str                # active 膠囊底色（半透明）
    progress_start: str
    progress_end: str
    text_primary: str
    text_secondary: str
    text_muted: str
    success: str
    warning: str
    error: str
    info: str
    # 幾何
    radius: int = 16
    radius_sm: int = 10
    gap: int = 16
    blur_enabled: bool = True


DARK_THEME = GlassTheme(
    name="dark",
    bg_gradient=["#17202C", "#0F161D", "#0A1014"],
    orb1_color="#802AA8A0",        # 青綠 50%
    orb2_color="#4A3D6AA8",        # 鋼藍 29%
    panel_bg="#8C0D131A",          # rgba(13,19,26,0.55)
    panel_bg_opaque="#FF111923",
    panel_blur=28,
    panel_border="#24FFFFFF",      # rgba(255,255,255,0.14)
    panel_highlight="#2EFFFFFF",   # rgba(255,255,255,0.18)
    panel_shadow="#66000000",      # rgba(0,0,0,0.4)
    accent="#2AA8A0",
    accent_fill="#592AA8A0",       # rgba(42,168,160,0.35)
    progress_start="#2AA8A0",
    progress_end="#5FC9D8",
    text_primary="#ECF5F3",
    text_secondary="#9FB8B3",
    text_muted="#7A938E",
    success="#5FD89A",
    warning="#E8C268",
    error="#E57373",
    info="#5FC9D8",
)

LIGHT_THEME = GlassTheme(
    name="light",
    bg_gradient=["#DCE8FF", "#F3E3FF", "#D8F1FF"],
    orb1_color="#737AA7FF",        # 藍 45%
    orb2_color="#73FF9AD5",        # 粉 45%
    panel_bg="#73FFFFFF",          # rgba(255,255,255,0.45)
    panel_bg_opaque="#FFF2F5FB",
    panel_blur=22,
    panel_border="#BFFFFFFF",      # rgba(255,255,255,0.75)
    panel_highlight="#E6FFFFFF",
    panel_shadow="#2E1C2740",
    accent="#4A7DFF",
    accent_fill="#4D4A7DFF",       # rgba(74,125,255,0.30)
    progress_start="#4A7DFF",
    progress_end="#9A5CFF",
    text_primary="#1C2740",
    text_secondary="#44507A",
    text_muted="#7583A8",
    success="#0A8F4E",
    warning="#B07D1A",
    error="#C62828",
    info="#2B6CB0",
)


def current_theme(page: ft.Page) -> GlassTheme:
    """唯一的深淺判斷入口 — 取代各 view 重複的 _is_dark_mode()。"""
    mode = getattr(page, "theme_mode", None)
    if mode == ft.ThemeMode.DARK:
        return DARK_THEME
    if mode == ft.ThemeMode.LIGHT:
        return LIGHT_THEME
    try:
        if page.platform_brightness == ft.Brightness.DARK:
            return DARK_THEME
    except Exception:
        pass
    return LIGHT_THEME


def _blur(theme: GlassTheme) -> ft.Blur | None:
    if not theme.blur_enabled:
        return None
    return ft.Blur(theme.panel_blur, theme.panel_blur, ft.BlurTileMode.MIRROR)


def glass_panel(
    content: ft.Control,
    theme: GlassTheme,
    *,
    padding: int | ft.Padding = 16,
    radius: int | None = None,
    expand: bool | int = False,
    width: int | None = None,
    height: int | None = None,
    on_click=None,
) -> ft.Container:
    """標準染色玻璃卡片。blur_enabled=False 時降級為不透明純色。"""
    blur = _blur(theme)
    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=theme.panel_bg if blur else theme.panel_bg_opaque,
        blur=blur,
        border=ft.border.all(1, theme.panel_border),
        border_radius=radius if radius is not None else theme.radius,
        shadow=ft.BoxShadow(
            blur_radius=24, spread_radius=0,
            color=theme.panel_shadow, offset=ft.Offset(0, 8),
        ),
        expand=expand,
        width=width,
        height=height,
        on_click=on_click,
        ink=on_click is not None,
    )


def glass_pill(
    text: str,
    theme: GlassTheme,
    *,
    primary: bool = False,
    on_click=None,
    width: int | None = None,
    disabled: bool = False,
) -> ft.Container:
    """膠囊按鈕。primary=True 用 accent_fill 底；否則低透明白底。"""
    fg = "#FFFFFF" if (primary and theme.name == "dark") else (
        theme.text_primary if primary else theme.text_secondary)
    return ft.Container(
        content=ft.Text(text, size=13, color=fg, weight=ft.FontWeight.W_600,
                        text_align=ft.TextAlign.CENTER),
        padding=ft.Padding(14, 8, 14, 8),
        bgcolor=theme.accent_fill if primary else "#12FFFFFF",
        border=ft.border.all(1, theme.panel_border),
        border_radius=999,
        on_click=on_click,
        ink=on_click is not None,
        width=width,
        disabled=disabled,
        animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
    )


def state_colors(theme: GlassTheme) -> dict[str, tuple[str, str]]:
    """步驟卡片狀態色 (bg, fg) — 取代 main_view._STATE_COLORS_*。"""
    if theme.name == "dark":
        return {
            "idle":    ("#12FFFFFF", theme.text_secondary),
            "running": (theme.accent_fill, "#FFFFFF"),
            "done":    ("#595FD89A", theme.success),
            "error":   ("#59E57373", theme.error),
        }
    return {
        "idle":    ("#40FFFFFF", theme.text_secondary),
        "running": (theme.accent_fill, theme.text_primary),
        "done":    ("#590A8F4E", theme.success),
        "error":   ("#59C62828", theme.error),
    }


def aurora_background(theme: GlassTheme, content: ft.Control) -> ft.Container:
    """漸層底 + 兩顆光暈 orb 的 Stack。content 疊在最上層。

    orb 漂移：orb Container 設 animate_position(7000, EASE_IN_OUT)，由
    flet_app 的既有背景 loop（page.run_task 計時器）每 7s 翻轉 top/left
    幾 px 實現往復。第一版為靜態，動畫在 Task 6 啟用。
    """
    orb1 = ft.Container(
        width=420, height=420, border_radius=999,
        gradient=ft.RadialGradient(colors=[theme.orb1_color, "#00000000"]),
        top=-140, right=-100,
        animate_position=ft.Animation(7000, ft.AnimationCurve.EASE_IN_OUT),
    )
    orb2 = ft.Container(
        width=340, height=340, border_radius=999,
        gradient=ft.RadialGradient(colors=[theme.orb2_color, "#00000000"]),
        bottom=-110, left=-80,
        animate_position=ft.Animation(7000, ft.AnimationCurve.EASE_IN_OUT),
    )
    return ft.Container(
        expand=True,
        gradient=ft.LinearGradient(
            begin=ft.Alignment(-0.6, -1), end=ft.Alignment(0.6, 1),
            colors=list(theme.bg_gradient), stops=[0.0, 0.5, 1.0],
        ),
        content=ft.Stack([orb1, orb2, content], expand=True),
    )


def glass_nav_item_colors(theme: GlassTheme, active: bool) -> tuple[str, str]:
    """(icon_bg, icon_color) for nav items."""
    if active:
        return theme.accent_fill, ("#FFFFFF" if theme.name == "dark" else theme.text_primary)
    return "#00000000", theme.text_secondary


def glass_nav(
    items: list[tuple[str, str]],   # [(icon_name, tooltip), ...] e.g. [(ft.Icons.HOME, "主頁"), ...]
    selected: int,
    on_change,                       # callable(index)
    theme: GlassTheme,
    github_url: str = GITHUB_URL,
) -> ft.Container:
    """懸浮玻璃側欄：四個頁籤直排 + 底部 GitHub 連結（spec 定案）。"""
    def _item(i: int, icon: str, tip: str) -> ft.Container:
        bg, fg = glass_nav_item_colors(theme, i == selected)
        return ft.Container(
            content=ft.Icon(icon, color=fg, size=22),
            bgcolor=bg, border_radius=theme.radius_sm,
            width=40, height=40, alignment=ft.Alignment(0, 0),
            tooltip=tip, ink=True,
            on_click=lambda e, n=i: on_change(n),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )

    async def _open_github(e):
        # page.launch_url 在 0.84 已 deprecated；UrlLauncher 是 async API。
        await ft.UrlLauncher().launch_url(github_url)

    col = ft.Column(
        [_item(i, ic, tip) for i, (ic, tip) in enumerate(items)]
        + [ft.Container(expand=True),
           ft.Container(
               content=ft.Icon(ft.Icons.CODE, color=theme.text_secondary, size=22),
               tooltip="GitHub", width=40, height=40, border_radius=theme.radius_sm,
               alignment=ft.Alignment(0, 0), ink=True, on_click=_open_github,
           )],
        spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return glass_panel(col, theme, padding=ft.Padding(8, 12, 8, 12), width=64)


def glass_dialog(theme: GlassTheme, title: str, content: ft.Control,
                 actions: list[ft.Control] | None = None) -> ft.AlertDialog:
    return ft.AlertDialog(
        title=ft.Text(title, color=theme.text_primary, size=16,
                      weight=ft.FontWeight.W_600),
        content=content,
        actions=actions or [],
        bgcolor=theme.panel_bg_opaque,   # 對話框疊在 barrier 上，blur 意義不大且耗效能 → 用不透明
        shape=ft.RoundedRectangleBorder(radius=theme.radius),
    )


def glass_snackbar(theme: GlassTheme, text: str) -> ft.SnackBar:
    return ft.SnackBar(
        content=ft.Text(text, color=theme.text_primary),
        bgcolor=theme.panel_bg_opaque,
        shape=ft.RoundedRectangleBorder(radius=theme.radius_sm),
    )
