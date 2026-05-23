# pixiv-batch-download

> Pixiv 批次下載器 — 以 **Flet（Material 3）** 重構，支援桌面與網頁模式。
>
> *Pixiv batch downloader rebuilt with Flet (Material 3), desktop & web.*

![主畫面](docs/screenshots/main-light.png)

---

## 功能概覽 Features

### 工作流程

- 4 步驟管線：步驟 1（追蹤畫師）→ 步驟 2（抓 PID）→ 步驟 3（抓 URL）→ 步驟 4（下載）
- 一鍵執行全流程（Run All）
- 每個步驟均可獨立暫停 / 繼續 / 停止

### Cookie 池與帳號排程

- 多組 Cookie 輪替，每組可設定別名與有效性測試
- `AccountScheduler`：冷卻時間依 `avg × ln(N+1)` 自動計算，避免觸發限速
- 每組 Cookie 可獨立綁定 Proxy（`http://`、`https://`、`socks5://`）

### 篩選規則

- 必須 tag / 禁止 tag（Chip 標籤，即時增刪）
- 一般 / R18 作品各自設定最低讚數門檻，支援特殊規則（per-tag 不同閾值）
- 篩選掉的 URL 保留在 SQLite，換條件重跑無需重新抓取

### 資料夾組織

- 依作者 ID 建立子資料夾
- R-18 / R-18G / AI 生成作品各自分類至獨立子資料夾
- 支援自訂檔名範本（`{timetag}_PID{pid}{page}{hashtag}.{ext}` 等佔位符）

### JXL 後處理（選用）

- 下載完成後自動用 `cjxl.exe` 轉換為 JPEG XL（無損）
- 可指定 effort（1–9）；不設定則自動搜尋已知安裝路徑

### 統計面板

- 本次 / 累計下載數、失敗數、運行時長
- 各 Cookie 帳號下載量長條圖（即時更新）

### 可靠性

- 遭遇 `ProxyError` / `ConnectTimeout` / `ConnectionError` 時，自動 60 秒重試最多 5 次
- 所有設定與進度透過 `safe_io.atomic_write_*` 寫入，最多保留 10 份歷史備份
- 每次執行前自動備份 SQLite 資料庫（使用官方 backup API，WAL 安全）

---

## 系統需求 Requirements

- Windows 10 / 11，Python 3.8+

```bash
pip install flet "requests[socks]"
```

> `flet` 內建 Dart runtime，無需額外安裝。  
> 需要 SOCKS5 proxy 支援時，務必安裝 `requests[socks]`。  
> JXL 轉換為選用功能，需另外下載 `cjxl.exe`（[libjxl 官方 release](https://github.com/libjxl/libjxl/releases)）。

---

## 執行方式 Usage

**桌面模式（預設）：**

```bash
python main.py
```

**網頁模式（瀏覽器開啟）：**

```bash
flet run app/gui/flet_app.py --web
```

---

## 典型使用流程 Typical Workflow

1. **設定**頁面：填寫 User ID、下載路徑、篩選規則、冷卻時間。
2. **Cookie** 頁面：新增 Cookie 字串，點選測試有效性。
3. **主頁**：依序執行步驟 1 → 4，或直接按「一鍵執行」。
4. **統計**頁面：查看即時進度與各帳號分配情況。

---

## 專案結構 Project Structure

```
app/
  entry/main.py          # Flet 啟動入口
  gui/
    flet_app.py          # 主視窗、NavigationRail、EventDispatcher
    dispatcher.py        # 事件佇列 → UI 回調（每 50ms 輪詢）
    run_actions.py       # RunController：組裝 AccountScheduler 並啟動執行緒
    views/
      main_view.py       # 步驟卡片、進度列、日誌面板
      settings_view.py   # 設定頁（ExpansionTile 分組）
      cookies_view.py    # Cookie 池管理
      stats_view.py      # 統計面板
  core/
    pixiv_thread_base.py # PauseableThread 基底（pause/resume/stop/retry）
    thread_following.py  # 步驟 1：抓追蹤畫師列表
    thread_pid_scan.py   # 步驟 2：抓作品 PID
    thread_url_fetch.py  # 步驟 3：抓圖片 URL / 元資料
    thread_download.py   # 步驟 4：下載圖片 / GIF
    account_scheduler.py # 多帳號冷卻排程（round-robin）
    metadata_db.py       # SQLite 元資料 + 待下載佇列
    pixiv_api.py         # Pixiv HTTP 封裝
    settings_store.py    # JSON 設定讀寫
    safe_io.py           # 原子寫入 + 歷史備份
    stats_collector.py   # 統計收集器
main.py                  # 根目錄入口（shim → app.entry.main）
```

---

## 資料儲存位置 Data Location

執行期設定與進度存於 `%APPDATA%/pixiv_download/`：

| 檔案 | 說明 |
|------|------|
| `othersettings.json` | 全域設定（路徑、篩選、冷卻等） |
| `cookies.json` | Cookie 池（含別名、Proxy 綁定、狀態） |
| `metadata.sqlite3` | PID 元資料、待下載佇列、已下載記錄 |

備份複製至同層 `history/` 目錄，命名格式 `filename.YYYYMMDD(.N)`，最多保留 10 份。

---

## 開發指引 Development

```bash
# 單元測試
pytest

# 整合測試（需要網路 / 真實 Cookie）
pytest -m integration

# 程式碼品質
ruff check app/
radon cc app/ -n C -s
lizard -C 15 -L 100 app/
```

---

## 截圖 Screenshots

### 主畫面 — 4 步驟管線 + 一鍵執行
![主畫面](docs/screenshots/main-light.png)

### Cookie 管理 — 多帳號輪替 + Proxy 綁定
![Cookie 管理](docs/screenshots/cookies-light.png)

> 圖中 alias 與 cookie 內容已遮蓋；實際使用會顯示你自訂的別名與最後檢查時間。

### 設定 — ExpansionTile 分組
![設定面板](docs/screenshots/settings-light.png)

### 統計面板 — 流量、JXL 節省空間、HTTP 請求、Cookie 分配
![統計面板](docs/screenshots/stats-light.png)

### 深色模式
![深色模式](docs/screenshots/main-dark.png)

> 右上角單鍵切換 LIGHT / DARK / SYSTEM，選擇透過 `SettingsStore` 持久化；所有 view 即時重新著色不需重啟。

---

## 注意事項 Notes

- 請遵守 Pixiv 服務條款及當地法律。
- 存取限制內容需要有效的登入 Cookie。
- 若遭遇頻率限制，請調高平均冷卻秒數（建議 ≥ 30 秒）。
