# pixiv-img-download

Pixiv 桌面下載器，以 **Flet（Material 3）** 重構，同時支援桌面與網頁模式。

## 功能概覽

**工作流程**

- 4 步驟管線：步驟 1（抓追蹤）→ 步驟 2（抓 PID）→ 步驟 3（抓 URL）→ 步驟 4（下載）
- 一鍵執行全流程（Run All）
- 每個步驟均可獨立暫停 / 繼續 / 停止

**Cookie 池與帳號排程**

- 支援多組 Cookie 同時輪替，每組可設定別名
- 內建有效性測試（單選 / 全選）
- `AccountScheduler` 依平均冷卻秒數（`avg × ln(N+1)`）自動分配請求配額，避免封號
- 每組 Cookie 可獨立綁定 Proxy（`http://`、`https://`、`socks5://`）

**篩選與下載控制**

- 必須 tag / 禁止 tag（Chip 標籤，即時新增刪除）
- 一般作品 / R18 作品各自設定最低讚數門檻
- 過濾 GIF、無 tag 不下載、無時間不下載
- 自訂檔名範本（`{timetag}_PID{pid}{page}{hashtag}.{ext}` 等佔位符）

**資料夾組織**

- 依作者 ID 建立子資料夾
- R-18 / R-18G / AI 生成作品各自分類至獨立子資料夾

**JXL 後處理（選用）**

- 下載完成後自動用 `cjxl.exe` 轉換為 JPEG XL（無損）
- 可指定壓縮 effort（1–9）；不設定則自動搜尋已知安裝路徑
- 可選是否刪除原始檔案

**統計面板**

- 本次 / 累計下載數、失敗數、運行時長
- 各 Cookie 帳號的下載量長條圖（即時更新）

**可靠性**

- 步驟 2/3/4 遭遇網路錯誤（`ProxyError` / `ConnectTimeout` / `ConnectionError`）時，自動以 60 秒間隔重試最多 5 次；全部失敗才停用該 Cookie
- 所有設定與進度檔案透過 `safe_io.atomic_write_*` 寫入，支援最多 10 份歷史備份

---

## 系統需求

- Windows 10 / 11
- Python 3.8+

安裝依賴：

```bash
pip install flet "requests[socks]"
```

> `flet` 內建 Dart runtime，無需額外安裝。如需 SOCKS5 proxy 支援，務必安裝 `requests[socks]`。

JXL 轉換為選用功能，需另外下載 `cjxl.exe`（libjxl 官方 release）。

---

## 執行方式

**桌面模式（預設）：**

```bash
python main.py
```

**網頁模式（瀏覽器開啟）：**

```bash
flet run app/gui/flet_app.py --web
```

---

## 典型使用流程

1. 在**設定**頁面填寫帳號 / User ID、下載路徑、篩選規則。
2. 在 **Cookie** 頁面新增有效的 `Cookie` 字串並測試。
3. 回到**主頁**，依序執行步驟 1 → 4，或直接按「一鍵執行」。
4. 下載期間可在**統計**頁面查看即時進度與各帳號分配情況。

---

## 專案結構

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
    pixiv_thread_base.py # PauseableThread 基底類別（pause/resume/stop/retry）
    thread_following.py  # 步驟 1：抓追蹤畫師列表
    thread_pid_scan.py   # 步驟 2：抓作品 PID
    thread_url_fetch.py  # 步驟 3：抓圖片 URL / 元資料
    thread_download.py   # 步驟 4：下載圖片 / GIF
    account_scheduler.py # 多帳號冷卻排程（round-robin）
    pixiv_api.py         # Pixiv HTTP 封裝與 Cookie 處理
    settings_store.py    # JSON 設定讀寫（含 legacy 遷移）
    safe_io.py           # 原子寫入 + 歷史備份
    stats_collector.py   # 統計資料收集器
    metadata_db.py       # SQLite 元資料快取
main.py                  # 根目錄入口（shim → app.entry.main）
```

---

## 資料儲存位置

所有執行期設定與進度檔案存於 `%APPDATA%/pixiv_download/`：

| 檔案 | 說明 |
|------|------|
| `othersettings.json` | 全域設定（路徑、篩選、冷卻等） |
| `cookies.json` | Cookie 池（含別名、Proxy 綁定、狀態） |
| `pictures_id.txt` | 已知 PID 清單（去重用） |
| `pixiv_info_cache.json` | 作品元資料快取 |

`atomic_write_*` 預設啟用備份，舊版本複製至同層 `history/` 目錄，命名格式為 `filename.YYYYMMDD(.N)`，最多保留 10 份。`cookies.json` 以 `backup=False` 寫入，不留歷史。

---

## 開發指引

**測試：**

```bash
pytest                          # 全部單元測試
pytest -m integration           # 需要網路 / 真實憑證的整合測試
```

**程式碼品質：**

```bash
ruff check app/                 # lint
radon cc app/ -n C -s           # 循環複雜度 ≥ C
lizard -C 15 -L 100 app/        # 認知複雜度 / 長函式警告
vulture app/ vulture_whitelist.py --min-confidence 80
```

---

## 注意事項

- 請遵守 Pixiv 服務條款及當地法律。
- 存取限制內容需要有效的登入 Cookie。
- 若遭遇頻率限制，請在設定中調高平均冷卻秒數（建議 ≥ 30 秒），或啟用「單執行緒 PID 模式」。
- 冷卻秒數設定低於 30 秒時，UI 會顯示警告。
