# 液態玻璃 UI 重設計規格

日期：2026-06-11
狀態：已與使用者於 visual companion 逐項確認（mockup 存於 `.superpowers/brainstorm/3902930-1781161308/content/`）

## 目標

解決現有四頁 GUI「亂、不統一、無章」的問題：建立單一設計系統模組，全
app（四個 view + 對話框 + SnackBar）統一改造為液態玻璃（liquid glass）
質感，支援深淺雙主題。

## 已確認的視覺決策

1. **雙主題**：沿用現有 `ui.theme_mode` 深/淺切換，兩套都做玻璃質感。
2. **深色主題＝「AB 融合版」**（使用者選定）：
   - 背景：石墨藍漸層 `linear-gradient(160deg, #17202C → #0F161D 50% → #0A1014)`
   - 光暈 orb：青綠 `#2AA8A0`（50% 不透明，右上）＋鋼藍 `#3D6AA8`（~29%，左下），緩慢漂移動畫（7s 往復、位移約 20px）
   - 玻璃面板：**深色染色玻璃**（使用者特別要求，修正光暈干擾文字的問題）
     `background rgba(13,19,26,0.55)`、blur 28、邊框 `rgba(255,255,255,0.14)`、
     內側上緣高光 `rgba(255,255,255,0.18)`、外陰影 `rgba(0,0,0,0.4)`
   - 強調色：青綠 `#2AA8A0`；進度條漸層 `#2AA8A0 → #5FC9D8`
   - 主文字 `#ECF5F3`，次文字 `#9FB8B3`，成功 `#5FD89A`
3. **淺色主題＝「霜玻璃」**：
   - 背景：`linear-gradient(135deg, #DCE8FF → #F3E3FF 40% → #D8F1FF)`
   - 光暈：藍 `#7AA7FF` 右上、粉 `#FF9AD5` 左下（~45% 不透明）
   - 玻璃面板：`rgba(255,255,255,0.45)`、blur 18-28、邊框 `rgba(255,255,255,0.75)`
   - 強調色：`#4A7DFF`；進度條漸層 `#4A7DFF → #9A5CFF`
   - 主文字 `#1C2740`，次文字 `#44507A`，成功 `#0A8F4E`
4. **導覽＝懸浮玻璃側欄**（取代 NavigationRail）：
   - 左側懸浮玻璃膠囊（圓角 16、與視窗邊緣留 16px 空隙），寬約 56-64px
   - 四個頁籤圖示直排；active 圖示有強調色底（深色 `rgba(42,168,160,.35)`）
   - **最下方放 GitHub 連結圖示**，點擊以 `page.launch_url` 開啟本專案 repo
5. **整體版面**：所有內容區改為「懸浮玻璃卡片群」，卡片間距 16-24px，
   圓角 16-18，浮在帶光暈的漸層背景上。

## 技術可行性（已驗證）

Flet 0.84 原生支援：`Container.blur`（背景模糊）、`LinearGradient` /
`RadialGradient`、`BoxShadow`、`border`、`animate*`。光暈漂移可用
`animate_position`/`animate_offset` + 定時翻轉目標實現（注意 flet-0-84-pitfalls
技能中的執行緒/更新規則）。做不到的：iOS 26 等級的折射變形 shader——以
「模糊 + 光暈 + 高光 + 陰影 + 平滑動畫」近似，已在 mockup 中經使用者認可。

## 架構：方案一（設計系統模組）

新增 **`app/gui/glass.py`** 作為唯一視覺來源：

### Tokens（`GlassTheme` dataclass，兩個實例 `DARK_THEME` / `LIGHT_THEME`）
- 背景漸層 stops、orb 配色/大小/位置
- 玻璃面板：fill 色、blur 值、border 色、highlight 色、shadow
- 強調色、進度漸層、文字三階（primary/secondary/muted）
- 語意色：success / warning / error / info（沿用現 log 顏色語意）
- 圓角（16/18/999）、間距尺度（8/16/24）、字級尺度

### 元件工廠（函式，回傳 Flet 控件）
- `aurora_background(theme)` — 漸層底 + 漂移 orb 的 Stack 底層
- `glass_panel(content, theme, ...)` — 標準染色玻璃卡片
- `glass_button(...)` / `glass_pill(...)` — 膠囊按鈕（主要/次要兩態）
- `glass_progress(value, theme)` — 漸層圓角進度條
- `glass_nav(items, on_change, github_url, theme)` — 懸浮側欄（含底部 GitHub 圖示）
- `glass_dialog(...)` / `glass_snackbar(...)` — 統一對話框/提示樣式
- `current_theme(page)` — 取代三個 view 各自重複的 `_is_dark_mode()`，
  回傳 `DARK_THEME` 或 `LIGHT_THEME`

### 遷移規則
- 四個 view 與 `flet_app.py`、`log_panel.py` 一律改從 `glass.py` 取得顏色
  與元件；**刪除**各檔案內的 `_STATE_COLORS_*`、`_CARD_COLORS_*`、
  `_BAR_COLORS_*`、`_STATUS_COLORS`、重複的 `_is_dark_mode()`。
- 功能零變更：所有控件的事件處理、ref、dispatcher 接線保持不動，只換
  視覺容器與樣式。
- 主題切換（AppBar 上的深淺切換）需讓玻璃元件即時換 theme：view 持有
  `current_theme(page)`，切換時呼叫各 view 既有的 reload/update 路徑。

## 各頁改造要點

- **flet_app.py**：頁面底層改 `aurora_background`；`NavigationRail` →
  `glass_nav`（含 GitHub 連結）；AppBar 改透明/玻璃化或併入內容區。
- **main_view.py**：步驟卡片（`_make_step_card`）、進度列、模式按鈕、
  暫停/停止控制全部換 glass 元件；log 面板容器玻璃化（內文 spans 不動）。
- **settings_view.py**：各 `_tile` 區塊 → `glass_panel`；輸入框/滑桿配
  色接 tokens；儲存 SnackBar → `glass_snackbar`。
- **cookies_view.py**：cookie 列卡片 → `glass_panel`；狀態徽章接語意色；
  編輯對話框 → `glass_dialog`。
- **stats_view.py**：統計卡片與長條圖配色全部接 tokens（移除自有色板）。
- **對話框/SnackBar**（main/settings/cookies 共 5 處）統一走 glass 版。

## 效能護欄

- blur 面板數量單頁 ≤ ~8；log 面板高頻更新時不得觸發整頁 rebuild
  （沿用現有 update 粒度）。
- orb 動畫限 2 顆、用 Flet 內建 animate（GPU 合成），不用 timer 逐幀。
- 若實測拖慢（舊機），tokens 留 `blur_enabled` 開關可整體降級為半透明
  純色（無 blur）。

## 測試

- `glass.py` 純函式可單元測試：theme 選擇（dark/light）、token 完整性、
  元件工廠回傳型別與關鍵屬性（blur 值、漸層 stops）。
- 各 view 既有測試不得壞；遷移後跑 `pytest -m "not integration"`。
- 視覺驗收：`flet run app/gui/flet_app.py --web` 人工核對 mockup。

## 並行實作分組（供新 session 以 subagents 併行）

- **Phase 0（串行，先行）**：建立 `app/gui/glass.py` + 單元測試。
  所有後續工作依賴它的 API，必須先完成並凍結介面。
- **Phase 1（四個 subagent 併行，互不碰同檔）**：
  - Agent A：`flet_app.py`（背景 + glass_nav + AppBar）
  - Agent B：`main_view.py` + `log_panel.py`
  - Agent C：`settings_view.py`
  - Agent D：`cookies_view.py` + `stats_view.py`
- **Phase 2（串行收尾)**：對話框/SnackBar 統一、刪除殘留死色板
  （vulture / grep 驗證）、全量測試、視覺走查兩主題四頁。
