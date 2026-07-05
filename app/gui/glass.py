"""Liquid-glass design system — the ONLY source of visual style for app/gui.

色值定案來源：docs/specs/2026-06-11-liquid-glass-ui-design.md。
所有 view 一律從本模組取得顏色與元件，禁止在 view 內硬編碼顏色。
"""
from __future__ import annotations

from dataclasses import dataclass

import flet as ft

GITHUB_URL = "https://github.com/Eric25741629/pixiv_follower_download"

# 全域字型：思源黑體（Noto Sans TC），隨 repo 附在 assets/fonts/NotoSansTC.ttf
# （flet_app 以 page.fonts 註冊）。深色背景上筆畫比微軟正黑體飽滿清晰。
# 字型檔不存在時退回微軟正黑體 UI（FONT_FALLBACK），不會壞版面。
FONT_FAMILY = "Noto Sans TC"
FONT_FALLBACK = "Microsoft JhengHei UI"
FONT_ASSET_PATH = "/fonts/NotoSansTC.ttf"   # 相對 assets_dir 的資產路徑


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
        border=ft.Border.all(1, theme.panel_border),
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


# 膠囊圖標：自繪 SVG（圓角向量），取代不齊的 emoji 字符 ⏸⏹▶。
# 24x24 viewBox；顏色由 style_pill 用前景色重染（重設 Image.src）。
_PILL_ICON_SHAPES = {
    "play": (
        '<path d="M9 6.2 L17.8 12 L9 17.8 Z" fill="{c}" stroke="{c}"'
        ' stroke-width="3" stroke-linejoin="round"/>'
    ),
    "pause": (
        '<rect x="6.4" y="5" width="4.2" height="14" rx="2.1" fill="{c}"/>'
        '<rect x="13.4" y="5" width="4.2" height="14" rx="2.1" fill="{c}"/>'
    ),
    "stop": '<rect x="6" y="6" width="12" height="12" rx="3" fill="{c}"/>',
}


def _pill_icon_svg(kind: str, color: str) -> bytes:
    shape = _PILL_ICON_SHAPES[kind].format(c=color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{shape}</svg>'
    ).encode()


def pill_icon(kind: str, color: str, size: int = 13) -> ft.Image:
    """一顆膠囊內嵌 SVG 圖標。``data`` 記住 kind，重染時重生 src。"""
    return ft.Image(
        src=_pill_icon_svg(kind, color), width=size, height=size, data=kind,
    )


def _pill_text(pill: ft.Container) -> ft.Text | None:
    """取出膠囊裡的 ft.Text（純文字或 Row[Image, Text] 兩種結構）。"""
    content = pill.content
    if isinstance(content, ft.Text):
        return content
    if isinstance(content, ft.Row):
        for c in content.controls:
            if isinstance(c, ft.Text):
                return c
    return None


def set_pill_label(pill: ft.Container, text: str) -> None:
    """改膠囊文字 — 不論 content 是 Text 還是 Row[Image, Text]。"""
    t = _pill_text(pill)
    if t is not None:
        t.value = text


def set_pill_icon(pill: ft.Container, kind: str) -> None:
    """換膠囊圖標（如 暫停⇄繼續），沿用目前文字的前景色。"""
    content = pill.content
    if not isinstance(content, ft.Row):
        return
    t = _pill_text(pill)
    fg = (t.color if t is not None else None) or "#FFFFFF"
    for c in content.controls:
        if isinstance(c, ft.Image):
            c.src = _pill_icon_svg(kind, fg)
            c.data = kind


def style_pill(pill: ft.Container, theme: GlassTheme, *, primary: bool = False) -> None:
    """就地重染一顆 glass_pill（主題切換時用，不重建控件）。"""
    fg = "#FFFFFF" if (primary and theme.name == "dark") else (
        theme.text_primary if primary else theme.text_secondary)
    pill.bgcolor = theme.accent_fill if primary else "#12FFFFFF"
    pill.border = ft.Border.all(1, theme.panel_border)
    if isinstance(pill.content, ft.Text):
        pill.content.color = fg
    elif isinstance(pill.content, ft.Row):
        for c in pill.content.controls:
            if isinstance(c, ft.Text):
                c.color = fg
            elif isinstance(c, ft.Image) and c.data in _PILL_ICON_SHAPES:
                c.src = _pill_icon_svg(c.data, fg)


def glass_pill(
    text: str,
    theme: GlassTheme,
    *,
    primary: bool = False,
    on_click=None,
    width: int | None = None,
    disabled: bool = False,
    icon: str | None = None,
) -> ft.Container:
    """膠囊按鈕。primary=True 用 accent_fill 底；否則低透明白底。

    ``icon`` 指定 ``_PILL_ICON_SHAPES`` 的 key（play/pause/stop）時，
    content 為 Row[Image, Text]；改字請走 :func:`set_pill_label`。
    """
    # no_wrap: a fixed-width pill must never wrap its label to a second
    # line — a wrapped label grows the pill vertically (oval pills bug).
    label = ft.Text(text, size=13, weight=ft.FontWeight.W_600,
                    text_align=ft.TextAlign.CENTER, no_wrap=True)
    if icon:
        content: ft.Control = ft.Row(
            [pill_icon(icon, "#FFFFFF"), label],
            spacing=7, tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
    else:
        content = label
    # 不可加 animate：flet 0.84 container.dart 的 ink 分支在 animate 非 None
    # 時走 AnimatedContainer，而該分支把 padding 重複套在外層（InkWell 內層
    # Container 已套過一次）→ 可點擊的膠囊比 disabled 的大一圈（disabled 不走
    # ink 路徑）。
    pill = ft.Container(
        content=content,
        padding=ft.Padding(14, 8, 14, 8),
        border_radius=999,
        on_click=on_click,
        ink=on_click is not None,
        width=width,
        disabled=disabled,
    )
    style_pill(pill, theme, primary=primary)
    return pill


class GlassProgressBar:
    """全圓角進度條（雙層 Container），取代 ft.ProgressBar。

    M3 LinearProgressIndicator 即使設了 border_radius，fill 交界處與
    track 兩端仍是直角切邊（track-gap 樣式），和玻璃語彙的全圓角衝突。
    這裡改用與統計頁長條圖相同的做法：圓角 track Container 內放一條
    圓角 fill Container，寬度用 Row flex（千分比）控制。

    介面模仿 ft.ProgressBar 的 value / color / bar_height / expand
    屬性，main_view 的呼叫點與既有測試不用改；放進版面的控件是
    ``.track``（wrapper 本身不是 Flet control）。
    """

    def __init__(self, *, color: str, bar_height: int = 12, radius: int = 6,
                 bgcolor: str = "#1FFFFFFF"):
        self.bar_height = bar_height
        self.expand = True
        self._value = 0.0
        self._fill = ft.Container(
            bgcolor=color, border_radius=radius, height=bar_height,
            expand=False, visible=False,
        )
        self._rest = ft.Container(expand=1000)
        self.track = ft.Container(
            content=ft.Row([self._fill, self._rest], spacing=0),
            bgcolor=bgcolor, border_radius=radius, height=bar_height,
            expand=True,
        )

    @property
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, v) -> None:
        try:
            f = float(v or 0.0)
        except (TypeError, ValueError):
            f = 0.0
        self._value = max(0.0, min(1.0, f))
        k = round(self._value * 1000)
        self._fill.visible = k > 0
        self._fill.expand = k if k > 0 else False
        self._rest.visible = k < 1000
        self._rest.expand = (1000 - k) if k < 1000 else False

    @property
    def color(self):
        return self._fill.bgcolor

    @color.setter
    def color(self, c) -> None:
        self._fill.bgcolor = c


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

    orb 是靜態的。舊版的 animate_position(7000) + 每 7s 翻轉位置讓 orb
    永遠處於補間中，玻璃面板的 backdrop blur 因此每一幀都要重算，
    實測閒置 GPU 約 7%；拿掉漂移後為 0%。裝飾動畫不值這個代價。
    """
    orb1 = ft.Container(
        width=420, height=420, border_radius=999,
        gradient=ft.RadialGradient(colors=[theme.orb1_color, "#00000000"]),
        top=-140, right=-100,
    )
    orb2 = ft.Container(
        width=340, height=340, border_radius=999,
        gradient=ft.RadialGradient(colors=[theme.orb2_color, "#00000000"]),
        bottom=-110, left=-80,
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
