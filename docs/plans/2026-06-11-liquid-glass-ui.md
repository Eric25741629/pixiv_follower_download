# 液態玻璃 UI 重設計實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **必讀**：實作前先讀 `docs/specs/2026-06-11-liquid-glass-ui-design.md`（視覺定案與色值來源）與 `.claude/skills/flet-0-84-pitfalls/SKILL.md`（Flet 0.84 陷阱，碰 app/gui/ 必讀）。
>
> **並行規則**：Task 1（glass.py）必須先完成並 commit，介面凍結後，Task 2/3/4/5 可由四個 subagent 併行（各自只碰自己的檔案，無交集）。Task 6 在全部完成後串行收尾。

**Goal:** 四個 view + 對話框統一改造為深淺雙主題液態玻璃設計系統，根除散落的硬編碼配色。

**Architecture:** 新增 `app/gui/glass.py` 為唯一視覺來源（GlassTheme tokens + 元件工廠）。各 view 僅換視覺容器與配色，事件處理、ref、dispatcher 接線全部不動。

**Tech Stack:** Flet 0.84（`Container.blur` / `LinearGradient` / `RadialGradient` / `BoxShadow` / `animate_position`，已驗證可用）、pytest。

**驗證基準（每個 task 完成時都要過）：**
```bash
pytest tests/ -m "not integration" -q     # 既有測試不得壞
python -c "import app.gui.flet_app"        # import 不炸
```

---

### Task 1【串行，最先做】：建立 glass.py 設計系統 + 單元測試

**Files:**
- Create: `app/gui/glass.py`
- Test: `tests/test_glass_theme.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_glass_theme.py
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_glass_theme.py -q`
Expected: FAIL（ModuleNotFoundError / ImportError）

- [ ] **Step 3: 實作 `app/gui/glass.py`**

```python
"""Liquid-glass design system — the ONLY source of visual style for app/gui.

色值定案來源：docs/specs/2026-06-11-liquid-glass-ui-design.md。
所有 view 一律從本模組取得顏色與元件，禁止在 view 內硬編碼顏色。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import flet as ft

GITHUB_URL = "https://github.com/<owner>/pixiv-img-download"  # 實作時以 git remote get-url origin 校正


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
    panel_highlight: str            # 內側上緣高光（BoxShadow inset 不支援 → 用 border 上緣近似，見 glass_panel）
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
        content=ft.Text(text, size=13, color=fg, text_align=ft.TextAlign.CENTER),
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
    on_dark = theme.name == "dark"
    return {
        "idle":    ("#12FFFFFF" if on_dark else "#40FFFFFF", theme.text_secondary),
        "running": (theme.accent_fill, "#FFFFFF" if on_dark else theme.text_primary),
        "done":    (theme.success + "59" if not theme.success.startswith("#") else "#59" + theme.success[1:], theme.success),
        "error":   ("#59" + theme.error[1:], theme.error),
    }


def aurora_background(theme: GlassTheme, content: ft.Control) -> ft.Container:
    """漸層底 + 兩顆光暈 orb 的 Stack。content 疊在最上層。

    orb 漂移：orb Container 設 animate_position(7000, EASE_IN_OUT)，由
    flet_app 的既有背景 loop（或 page.run_task 計時器）每 7s 翻轉 top/left
    幾 px 實現往復。第一版可先靜態，動畫在 Task 6 啟用。
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

    def _open_github(e):
        e.page.launch_url(github_url)

    col = ft.Column(
        [_item(i, ic, tip) for i, (ic, tip) in enumerate(items)]
        + [ft.Container(expand=True),
           ft.Container(
               content=ft.Image(src="github-mark.svg", width=22, height=22)
               if False else ft.Icon(ft.Icons.CODE, color=theme.text_secondary, size=22),
               tooltip="GitHub", width=40, height=40, border_radius=theme.radius_sm,
               alignment=ft.Alignment(0, 0), ink=True, on_click=_open_github,
           )],
        spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return glass_panel(col, theme, padding=ft.Padding(8, 12, 8, 12), width=64)


def glass_dialog(theme: GlassTheme, title: str, content: ft.Control,
                 actions: list[ft.Control] | None = None) -> ft.AlertDialog:
    return ft.AlertDialog(
        title=ft.Text(title, color=theme.text_primary, size=16),
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
```

注意：`state_colors` 的 done/error 行有一個寫死轉換（`"#59" + hex[1:]` = 35% alpha fill）；實作時請直接寫 `("#595FD89A", theme.success)`、`("#59E57373", theme.error)`（深色）/ 對應淺色值，刪掉那行條件式雜技，測試只驗證鍵齊全與 #開頭。

`GITHUB_URL`：實作時執行 `git remote get-url origin` 取真實網址填入；若無 remote，問使用者。

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_glass_theme.py -q`
Expected: PASS（5 tests）

- [ ] **Step 5: Flet API 自檢（0.84 相容性）**

Run: `python -c "from app.gui.glass import *; import flet as ft; p=glass_panel(ft.Text('x'),DARK_THEME); n=glass_nav([(ft.Icons.HOME,'主頁')],0,lambda i:None,DARK_THEME); a=aurora_background(DARK_THEME, ft.Text('y')); print('ok')"`
Expected: `ok`（若任何屬性在 0.84 不存在會在此炸掉；修正方式查 flet-0-84-pitfalls 技能）

- [ ] **Step 6: Commit**

```bash
git add app/gui/glass.py tests/test_glass_theme.py
git commit -m "feat(gui): liquid-glass design system (glass.py) with dual themes"
```

---

### Task 2【併行 Agent A】：flet_app.py — 背景、玻璃側欄、AppBar

**Files:**
- Modify: `app/gui/flet_app.py`（nav_rail 在 505-531 行、on_nav_change 476-498、`_activate_view` 128-138、AppBar 672-683、頁面組裝 ~680-800）

**前置**：Task 1 已 commit。只碰本檔，不碰 views/。

- [ ] **Step 1: 匯入並建立 theme**

`main(page)` 開頭（約 276 行設定主題色附近）加：
```python
from app.gui.glass import current_theme, aurora_background, glass_nav, DARK_THEME, LIGHT_THEME
theme = current_theme(page)
```

- [ ] **Step 2: NavigationRail → glass_nav**

刪除 505-531 的 `nav_rail = ft.NavigationRail(...)`，改為：
```python
_nav_items = [
    (ft.Icons.HOME_OUTLINED, "主頁"),
    (ft.Icons.SETTINGS_OUTLINED, "設定"),
    (ft.Icons.COOKIE_OUTLINED, "Cookie"),
    (ft.Icons.BAR_CHART_OUTLINED, "統計"),
]
_nav_selected = [0]

def _on_glass_nav(idx: int) -> None:
    class _E:  # 兼容原 on_nav_change(e) 介面：e.control.selected_index
        class control:
            selected_index = idx
    _nav_selected[0] = idx
    on_nav_change(_E())          # 沿用既有防抖/切頁邏輯（476-498 行，不動）
    _rebuild_nav()

def _rebuild_nav() -> None:
    nav_holder.content = glass_nav(_nav_items, _nav_selected[0], _on_glass_nav, theme)
    nav_holder.update()

nav_holder = ft.Container()
_rebuild_nav_initial = glass_nav(_nav_items, 0, _on_glass_nav, theme)
nav_holder.content = _rebuild_nav_initial
```
實作時依 on_nav_change 的實際簽名調整 `_E` 兼容層；若它只讀 `e.control.selected_index`，上述即可。若改寫更乾淨（讓 on_nav_change 直接吃 int），允許重構但不得改動防抖與 `_activate_view` 行為。

- [ ] **Step 3: 頁面組裝改為 aurora 背景 + 懸浮版面**

原本 `page.add(ft.Row([nav_rail, divider, content...]))` 之類的根組裝改為：
```python
root = ft.Row(
    [
        ft.Container(nav_holder, padding=ft.Padding(16, 16, 0, 16)),
        ft.Container(views_stack, expand=True, padding=16),  # views_stack=現有四 view 的容器
    ],
    expand=True, spacing=0,
)
page.add(aurora_background(theme, root))
```
注意：四個 view 保持掛載、用 `.visible` 切換的機制（128-138 行）不動。

- [ ] **Step 4: AppBar 玻璃化**

672-683 的 `page.appbar`：保留現有按鈕（深淺切換等），改 `bgcolor="#00000000"`，文字/圖示色接 `theme.text_primary`。深淺主題切換的 handler 在切換後需重建 theme 相依控件：最低限度做法 — 切換後呼叫 `page.window.close`？不可。正確做法：theme 切換 handler 內重新計算 `theme = current_theme(page)` 並更新 aurora 背景容器的 gradient/orb 色與 nav（`_rebuild_nav()`），views 的主題刷新沿用其既有 reload 路徑（`_reload_views_on_loop`，464-474 行）。

- [ ] **Step 5: 驗證**

Run: `pytest tests/ -m "not integration" -q` → PASS（無新增失敗）
Run: `python -c "import app.gui.flet_app"` → 無錯誤
人工：`flet run app/gui/flet_app.py --web`，確認背景漸層 + 光暈 + 玻璃側欄顯示、四頁可切換、GitHub 圖示開啟連結、深淺切換不炸。

- [ ] **Step 6: Commit**

```bash
git add app/gui/flet_app.py
git commit -m "feat(gui): aurora background + floating glass nav with GitHub link"
```

---

### Task 3【併行 Agent B】：main_view.py + log_panel.py

**Files:**
- Modify: `app/gui/views/main_view.py`（色板 20-49、步驟卡 240-269、進度列 490-525、模式按鈕 434-437、build 800-833、loading dialog 146-158）
- Modify: `app/gui/log_panel.py`（容器樣式 33-88）

- [ ] **Step 1: 刪除自有色板，接 glass**

刪 `_STATE_COLORS_LIGHT/_STATE_COLORS_DARK/_is_dark_mode/_state_palette`（20-49 行），改：
```python
from app.gui.glass import current_theme, state_colors, glass_panel, glass_pill, glass_dialog

def _state_palette(page: ft.Page) -> dict[str, tuple[str, str]]:
    return state_colors(current_theme(page))
```
（保留 `_state_palette` 名稱，呼叫端 241/266 行零改動。）

- [ ] **Step 2: 步驟卡玻璃化**

`_make_step_card`（240-261）：`ft.Card(content=container)` → 直接回傳玻璃化 container：
```python
container = ft.Container(
    content=text, padding=12, bgcolor=bg,
    border_radius=current_theme(self._page).radius_sm,
    border=ft.border.all(1, current_theme(self._page).panel_border),
    width=110, alignment=ft.Alignment(0, 0), ink=True,
    animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
    on_click=lambda e, n=index + 1: self._on_run_step(n),
)
...
return container   # 不再包 ft.Card
```
`set_step_state`（263-269）邏輯不變（仍寫 `bgcolor`/`color`）。

- [ ] **Step 3: 進度列與控制按鈕**

`_make_progress_row`（490-525）：ProgressBar 改 `color=theme.accent, bgcolor="#1FFFFFFF"`，圓角維持 `_PROG_BAR_RADIUS`。模式按鈕（434-437）與暫停/停止鈕改用 `glass_pill`（primary=當前選中）。所有 `ft.Colors.*` 引用換成 theme tokens（成功→`theme.success`、錯誤→`theme.error`、次要文字→`theme.text_secondary`）。

- [ ] **Step 4: build() 區塊包進 glass_panel**

`build()`（800-833）：進度/步驟區與 log 區各包一層 `glass_panel(..., theme, expand=...)`，卡片間距 `theme.gap`。loading dialog（146-158）→ `glass_dialog(theme, ...)`（actions 不變）。

- [ ] **Step 5: log_panel.py 玻璃化**

`__init__`（33-88）內的外層容器：移除實色 `bgcolor`，由 main_view 的 glass_panel 包裹提供視覺；內部僅保留捲動/選取邏輯。日誌文字色維持 html_to_spans 的語意色（不動 log_format.py）。**注意效能護欄**：log 高頻 append 的 update 粒度維持現狀（只 update 自身控件，不觸發整頁）。

- [ ] **Step 6: 驗證 + Commit**

Run: `pytest tests/ -m "not integration" -q` → PASS
Run: `python - <<'PY'` 快檢 `from app.gui.views.main_view import MainView` 可 import。
人工走查：步驟卡四態變色、進度條、暫停/恢復按鈕文字、log 顏色。
```bash
git add app/gui/views/main_view.py app/gui/log_panel.py
git commit -m "feat(gui): glass main view and log panel"
```

---

### Task 4【併行 Agent C】：settings_view.py

**Files:**
- Modify: `app/gui/views/settings_view.py`（`_tile` 591-598、build 590-704、冷卻警告 dialog 568-577、SnackBar 561/584、警告色 198）

- [ ] **Step 1: `_tile` → glass_panel**

build() 內部的 `_tile()` helper（591-598）改為呼叫 `glass_panel(content, current_theme(self._page), padding=16)`，標題文字色 `theme.text_primary`、說明文字 `theme.text_secondary`。

- [ ] **Step 2: 輸入控件配色**

TextField/Dropdown/Slider/Switch 統一：`border_color=theme.panel_border`、`focused_border_color=theme.accent`、`active_color=theme.accent`（Slider/Switch）。冷卻警告（198 行的 `ft.Colors.*`）→ `theme.warning`。

- [ ] **Step 3: 對話框與 SnackBar**

568-577 的冷卻警告 AlertDialog → `glass_dialog(theme, ...)`；561/584 的儲存 SnackBar → `glass_snackbar(theme, ...)`。show/pop 呼叫路徑不變（flet-0-84-pitfalls 的 dialog 規則）。

- [ ] **Step 4: 驗證 + Commit**

Run: `pytest tests/ -m "not integration" -q` → PASS；import 自檢同前。
人工：每個設定 tile 是玻璃卡、儲存提示樣式正確、深淺兩主題下文字可讀。
```bash
git add app/gui/views/settings_view.py
git commit -m "feat(gui): glass settings view"
```

---

### Task 5【併行 Agent D】：cookies_view.py + stats_view.py

**Files:**
- Modify: `app/gui/views/cookies_view.py`（`_STATUS_COLORS` 18-23、列卡片 149-249、狀態徽章 140-147、編輯 dialog 378-386、build 510-535）
- Modify: `app/gui/views/stats_view.py`（色板 11-45、`_is_dark_mode` 50-59、卡片 163-189、長條圖 259-280）

- [ ] **Step 1: cookies — 語意色接 tokens**

刪 `_STATUS_COLORS`（18-23），改：
```python
from app.gui.glass import current_theme, glass_panel, glass_dialog, glass_pill

def _status_colors(page) -> dict[str, str]:
    t = current_theme(page)
    return {"valid": t.success, "invalid": t.error, "untested": t.text_muted,
            "testing": t.info, "disabled": t.warning}
```
（鍵名以現檔 18-23 行實際鍵為準，逐一對映語意。）

- [ ] **Step 2: cookies — 列卡片與對話框**

`_build_cookie_row`（149-249）外層容器 → `glass_panel(..., padding=12, radius=theme.radius_sm)`；狀態徽章（140-147）用語意色 + 35% alpha 底（如 `"#59" + color[1:]`）。編輯 dialog（378-386）→ `glass_dialog`。新增/移除/測試按鈕 → `glass_pill`。

- [ ] **Step 3: stats — 刪自有色板**

刪 `_CARD_COLORS_*`、`_BAR_COLORS_*`、`_is_dark_mode`、`_card_palette`、`_bar_palette`（11-80 行）。統計卡 → `glass_panel`，卡片數值色用 `theme.accent` 系；長條圖條色循環使用 `[theme.accent, theme.info, theme.success, theme.warning]`，底色 `"#1FFFFFFF"`。

- [ ] **Step 4: 驗證 + Commit**

Run: `pytest tests/ -m "not integration" -q` → PASS；import 自檢兩檔。
人工：cookie 列玻璃卡 + 徽章五態、統計卡與長條圖兩主題可讀。
```bash
git add app/gui/views/cookies_view.py app/gui/views/stats_view.py
git commit -m "feat(gui): glass cookies and stats views"
```

---

### Task 6【串行收尾，Task 2-5 全部完成後】

**Files:**
- Modify: `app/gui/flet_app.py`（orb 漂移啟用）
- Modify: 全 gui 目錄（死碼清理）

- [ ] **Step 1: 啟用 orb 漂移動畫**

在 flet_app 既有的背景刷新 loop（`_reload_views_on_loop` 同層）加一個 7s 週期任務：翻轉兩顆 orb 的 `top/left` ±20px 後 `update()`，靠 `animate_position` 平滑過渡。**必須**走 `page.run_task`/既有 loop，不得開裸 thread 碰控件（flet-0-84-pitfalls）。

- [ ] **Step 2: 死色板清理驗證**

Run: `grep -rn "_STATE_COLORS\|_CARD_COLORS\|_BAR_COLORS\|_STATUS_COLORS\|_is_dark_mode" app/gui/ --include="*.py"`
Expected: 僅 `glass.py` 內（若有）；各 view 零殘留。
Run: `vulture app/ vulture_whitelist.py --min-confidence 80` → 無新增 gui 死碼。

- [ ] **Step 3: 全量驗證**

```bash
pytest tests/ -m "not integration" -q        # 全綠
ruff check app/gui/                           # 無新增告警
```
人工兩主題 × 四頁走查 + 跑一次 Step 1（小規模）確認 log/進度/dialog 即時更新無卡頓。若 blur 造成可感卡頓：把 `blur_enabled=False` 試跑對照，並在 settings 加開關（後續 PR，不在本計畫）。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(gui): enable aurora drift, remove dead palettes, finish liquid glass migration"
```

---

## Subagent 併行調度建議（給執行 session）

1. 先以一個 subagent（或 inline）完成 Task 1，commit 後凍結 `glass.py` 介面。
2. 同一訊息併發四個 subagent：Task 2（flet_app）、Task 3（main_view+log_panel）、Task 4（settings）、Task 5（cookies+stats）。檔案零交集，可安全並行；若用 git worktree 隔離，合併時僅需處理零衝突合併。
3. 四者全部回報後，串行執行 Task 6。
4. 任何 agent 碰到 Flet API 不存在/行為怪異：先查 `.claude/skills/flet-0-84-pitfalls/SKILL.md` 再修，不得自行猜 API。
