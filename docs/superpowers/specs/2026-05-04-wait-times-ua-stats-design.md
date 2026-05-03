# 設計文件：等待時間可調 + Chrome UA 偵測 + 免 Cookie 統計

日期：2026-05-04

## 背景

三個問題需要解決：
1. 同一 PID 多頁下載之間的等待時間（intra-PID sleep）預設 1~3 秒，太短，且 UI 無法調整
2. 免 Cookie 請求的等待時間雖有設定鍵但 UI 沒有欄位，且統計不獨立顯示
3. User-Agent 全部硬編碼，無法從設定頁調整或自動偵測瀏覽器版本

---

## Section 1：設定鍵與預設值

所有鍵存放於 `performance` 區段（`app/core/settings_store.py`）。

| 鍵名 | 狀態 | 新預設值 | 說明 |
|---|---|---|---|
| `intra_pid_wait_min` | 新增 | 5 | 同 PID 多頁之間最短等待（秒） |
| `intra_pid_wait_max` | 新增 | 15 | 同 PID 多頁之間最長等待（秒） |
| `pid_wait_nocookie_min` | 已存在，無 UI | 3 | 免 Cookie 請求最短等待（秒） |
| `pid_wait_nocookie_max` | 已存在，無 UI | 8 | 免 Cookie 請求最長等待（秒） |

`auth.agent` 已存在，繼續用來存 UA 字串。

**run_actions.py 的接線修正**：`app/gui/run_actions.py:444~445` 目前把
`pid_wait_nocookie_min/max` 餵給 `intra_pid_wait_min/max`，改後各自讀獨立的鍵。

---

## Section 2：Settings UI

### 「冷卻設定」Tile 重構（修復 Slider label 與 hint text 重疊）

現狀：`ft.Row([self._tf_cooldown, self._sl_cooldown])` — Slider 懸浮 label 和下方
`_label_cooldown_hint` 在 Flet 0.84 垂直空間不足，互相壓疊。

新佈局（Slider 獨佔一行）：

```
_tile("冷卻設定", [
    ft.Row([tf_cooldown, label_cooldown_hint], spacing=12),   # TextField + hint 同行
    sl_cooldown,                                               # Slider 獨行，有足夠浮空間
    ft.Row([ft.Text("同 PID 頁間等待（秒）"), tf_intra_min, ft.Text("~"), tf_intra_max]),
    ft.Row([ft.Text("免 Cookie 請求等待（秒）"), tf_nocookie_min, ft.Text("~"), tf_nocookie_max]),
    sw_single_thread,
])
```

新增控件：
- `_tf_intra_min` / `_tf_intra_max`：TextField，width=80，keyboard_type=NUMBER
- `_tf_nocookie_min` / `_tf_nocookie_max`：TextField，width=80，keyboard_type=NUMBER
- 儲存時驗證 min ≥ 1 且 min ≤ max，不符合則 clamp

### User-Agent 區塊（新 ExpansionTile，放在 Proxy 設定旁）

```
▶ User-Agent 設定
  [TextField: 可編輯，寬度 expand]  [重新偵測 Chrome 按鈕]
  hint_text="未設定，將使用內建隨機 UA"
```

- 儲存時將 TextField 值存入 `auth.agent`
- 「重新偵測 Chrome」按鈕呼叫 `detect_chrome_ua()`（同步）
  - 成功：更新 TextField，SnackBar「已從登錄檔偵測到 Chrome {version}，UA 已更新」
  - 失敗：TextField 不變，SnackBar「找不到 Chrome 安裝（已檢查登錄檔與 AppData），請手動填寫 UA」

---

## Section 3：Chrome UA 偵測模組

新檔：`app/core/chrome_detect.py`

函式簽章：`detect_chrome_ua() -> str | None`

偵測順序：
1. `winreg` 讀 `HKCU\Software\Google\Chrome\BLBeacon\version`
2. `winreg` 讀 `HKLM\SOFTWARE\Google\Chrome\BLBeacon\version`
3. 掃 `%LOCALAPPDATA%\Google\Chrome\Application\` 取最新版號資料夾名稱（格式 `124.0.x.y`）
4. 全部失敗 → 回傳 `None`

成功時回傳完整 UA 字串：
```
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36
```

**呼叫時機**：只在使用者按下「重新偵測 Chrome」按鈕時執行，app 啟動不自動偵測（避免覆蓋手動值）。

`pixiv_api.py` 的 `Agent` 參數傳遞鏈不動，`auth.agent` 透過現有 `run_actions.py` / `user_info.py` 讀取並傳入。

---

## Section 4：免 Cookie 統計

**不改 StatsCollector 核心邏輯。**

`"免Cookie"` label 在 `thread_url_fetch.py` 已存在，StatsCollector 已按 label 累計。

改動點：
1. **圖表/統計顯示**：彙整時將 key 為 `"免Cookie"` 的 entry 從 cookie 帳號列表中分離，
   單獨渲染成 `ft.Text("免 Cookie：{n} 次")`，放在圖表下方或旁邊
2. **Step 3 完成摘要**：在現有 Step 3 summary 那行加入
   `f"免 Cookie 查詢：{self._step3_cookie_req_counts['free']} 次"` 顯示

---

## 影響範圍

| 檔案 | 改動類型 |
|---|---|
| `app/core/settings_store.py` | 新增 4 個設定鍵及預設值 |
| `app/core/chrome_detect.py` | 新檔：Chrome UA 偵測函式 |
| `app/gui/views/settings_view.py` | 重構冷卻 Tile 佈局、新增 min/max TextField、新增 UA ExpansionTile |
| `app/gui/run_actions.py` | 修正 intra_pid_wait 接線（改讀獨立鍵） |
| `app/gui/views/` (統計顯示) | 分離免 Cookie 計數顯示 |
| `app/core/thread_url_fetch.py` | Step 3 摘要加免 Cookie 查詢次數 |

---

## 不在本次範圍

- 排序下載順序（另議）
- headers 除 User-Agent 外的其他欄位
- 非 Windows 平台的 Chrome 偵測
