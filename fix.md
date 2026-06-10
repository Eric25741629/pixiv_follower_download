# 最嚴格程式碼評估報告 — pixiv-img-download

## 總覽

| 維度 | CRITICAL | HIGH | MEDIUM | LOW |
|------|----------|------|--------|-----|
| **安全漏洞** | 2 | 4 | 5 | 4 |
| **並發/執行緒** | 4 | 3 | 5 | 3 |
| **程式碼品質** | 3 | 4 | 3 | 4 |
| **錯誤處理/韌性** | 5 | 7 | 6 | 4 |
| **架構設計** | 3 | 2 | 2 | 1 |
| **合計** | **17** | **20** | **21** | **16** |

---

## 🚨 CRITICAL (17) — 必須立即修復

### 安全

#### C1. 硬編碼完整 session cookie
- **位置:** `app/core/pixiv_api.py:589,895,898-899,916,920,930,935`
- **描述:** 多個完整 Pixiv session cookie (PHPSESSID, device_token, __cf_bm) 以明文硬編碼在註解和死碼區塊中。即使在 `__main__` 區塊中，提交到 repo 後任何有存取權限的人都能看到。
- **影響:** 若 token 仍有效則帳號被接管；即使移除，git history 中仍存在。
- **修復:** 立即移除所有硬編碼 cookie，撤銷所有已暴露的 session，使用 `git filter-branch` 清除歷史。

#### C2. SSL 驗證全域禁用
- **位置:** `app/core/pixiv_api.py:12,27` — `urllib3.disable_warnings()` + `sess.verify = False`；`app/core/proxy_utils.py:71` — `verify=False`
- **描述:** 透過 `make_session()` 發出的所有 HTTP 請求都完全禁用了憑證驗證。`urllib3` 警告被壓制。所有流量容易遭受中間人攻擊，尤其在傳輸 cookie 等憑證時非常危險。
- **影響:** 網路上的攻擊者可截取 Pixiv session cookie、下載 URL 和 proxy 憑證。
- **修復:** 移除 `sess.verify = False`，使用 bundled `certs.pem` 或系統 CA store。若需自訂 CA，使用 `sess.verify = '/path/to/ca.pem'`。

### 並發

#### C3. 全域 `pid_num` / `pid_len` 無同步
- **位置:** `thread_pid_scan.py:27-30,65,304-305,512-514,646-648`；`thread_following.py:65-66`
- **描述:** 模組層級全域變數 `pid_num` 和 `pid_len` 從多個執行緒無任何鎖地讀寫。`ThreadPoolExecutor` workers 執行 `pid_num = pid_num + 1` (line 648) 是典型的 lost-update race condition。
- **影響:** 進度計數器不確定；多執行緒執行時會少算/多算。
- **修復:** 使用 `threading.Lock` 保護所有對 `pid_num`/`pid_len` 的讀寫，或改用 `threading.atomic` 計數器。

#### C4. Cookie 使用計數 dict/set 無鎖修改 (TOCTOU)
- **位置:** `thread_pid_scan.py:121-134`；`app/core/pixiv_thread_base.py:190-198`
- **描述:** 多個 `ThreadPoolExecutor` workers 同時呼叫 `_record_step2_cookie_usage`，修改 `set` 和 `dict` 而無鎖。`if aid_key not in ... .add(...)` + `counts[label] = int(counts.get(label, 0)) + 1` 的 check-then-act 不是原子操作。
- **影響:** cookie 使用計數可能不準確。
- **修復:** 使用 `threading.Lock` 保護所有 cookie 使用計數的讀寫操作。

#### C5. `url_meta` dict 在多執行緒 pool 中無鎖讀取
- **位置:** `thread_download.py:2006` (`_execute_downloads_pool`)
- **描述:** 4 個 `ThreadPoolExecutor` workers 呼叫 `_download_pid_group` → `_resolve_pid_and_cookie` → `_get_meta` 讀取 `self.url_meta`。同時 `_mark_gif_cookie_usage` (line 2443) 在 `_url_meta_lock` 下寫入 `self.url_meta[pid_key]`。但 `_get_meta` 讀取時**未獲取 `_url_meta_lock`**。
- **影響:** worker 可能讀到半寫入的 dict entry。
- **修復:** `_get_meta` 應獲取 `_url_meta_lock` 後再讀取 dict。

#### C6. `download_time` 在鎖外讀取 → 重複 timetag
- **位置:** `thread_download.py:2488-2489,2651-2655,2602`
- **描述:** `_jpg_advance_timetag` 和 `_stream_ugoira_zip_bytes` 在 `self.timelock` 下遞增 `self.download_time`。但 `gif_download` 在鎖外讀取 `self.download_time` (line 2602)。兩個 worker 可同時讀取相同值並產生重複 timetag。
- **影響:** 檔名衝突 — 兩個 GIF 以相同時間戳儲存。
- **修復:** `gif_download` 中讀取 `download_time` 時也應獲取 `timelock`。

### 品質

#### C7. `main()` 函數 486 行，CC=26
- **位置:** `app/gui/flet_app.py:208`
- **描述:** 486 行的單一函數處理 UI 建構、event dispatch 接線、session recovery、主題切換、視窗關閉 hook、worker 生命週期。圈複雜度 26，遠超合理範圍。
- **影響:** 極難測試、維護和除錯。
- **修復:** 分解為 `_setup_ui()`, `_wire_events()`, `_handle_session_recovery()`, `_setup_theme()`, `_setup_window_close()` 等子函數。

#### C8. Bare `except:` 攔截 KeyboardInterrupt/SystemExit
- **位置:** `app/core/pixiv_api.py:343`、`app/core/pixiv_api.py:881`
- **描述:** 兩處 bare `except:` 會攔截所有異常包括 `KeyboardInterrupt`、`SystemExit`、`GeneratorExit`。line 881 的 handler 還使用 `open()` 而非 `with` 區塊，若 `write()` 拋異常則檔案永遠不會關閉。
- **影響:** Ctrl+C 無法終止程式；檔案描述符洩漏。
- **修復:** 改為 `except Exception:` 並使用 `with` 區塊管理檔案。

#### C9. `thread_download.py` 2803 行 God Class
- **位置:** `app/core/thread_download.py`
- **描述:** 單一類別包含 80+ 方法，混合網路請求、檔案系統操作、資料庫互動、過濾邏輯、進度回報、JXL 轉換、ugoira 處理。
- **影響:** 極難理解、測試和維護。
- **修復:** 拆分為 `download_executor.py`、`jxl_converter.py`、`ugoira_handler.py`、`filename_builder.py` 等子模組。

### 錯誤處理

#### C10. Selenium driver 建立後從未 quit
- **位置:** `app/core/pixiv_api.py:294-415` (auto_get_cookie)、`app/core/pixiv_api.py:451` (get_author_picture_ids)、`app/core/pixiv_api.py:285` (logging)
- **描述:** `driver = webdriver.Chrome(options=option)` 建立後從未呼叫 `driver.quit()`。`driver.close()` 只關閉視窗不終止 session。每次呼叫都會洩漏一個 Chrome 行程。
- **影響:** 記憶體和行程洩漏，最終耗盡系統資源。
- **修復:** 使用 `try/finally` 確保 `driver.quit()` 被呼叫，或使用 context manager。

#### C11. `subprocess.run` 執行 cjxl 無 timeout
- **位置:** `app/core/thread_download.py:1021`
- **描述:** `subprocess.run(cmd, capture_output=True, text=True, check=False)` 無 timeout 參數。若 `cjxl.exe` 處理損壞圖片時掛起，JXL worker 執行緒永遠阻塞。
- **影響:** JXL 轉換靜默停止。
- **修復:** 加入 `timeout=120` (2 分鐘) 或可配置的 timeout 值。

#### C12. 所有非 proxy HTTP 錯誤都回傳 `[404]`
- **位置:** `app/core/pixiv_api.py:776-784`
- **描述:** `Pixiv_info._fetch` 中，非 proxy 異常 (包括 `ReadTimeout`, `ChunkedEncodingError`, `SSLError` 等暫時性錯誤) 全部回傳 `[404], False, status`。呼叫者無法區分暫時性錯誤和真正的 404。
- **影響:** 暫時性網路問題導致作品被標記為不存在，永久跳過。
- **修復:** 區分暫時性錯誤 (timeout, SSL, chunked) 和永久錯誤 (404, 401)，暫時性錯誤應重試。

#### C13. `os.getenv('APPDATA')` 可能為 None
- **位置:** `app/core/pixiv_api.py:707`、`app/core/thread_download.py:119`、`app/core/thread_pid_scan.py:27` 等 10+ 處
- **描述:** `os.getenv('APPDATA') + r'/pixiv_download/'` — 在非 Windows 或受限環境中 `APPDATA` 為 `None`，`None + str` 拋出 `TypeError`。
- **影響:** 非 Windows 環境直接崩潰。
- **修復:** 使用 `pathlib.Path` 和 `os.getenv('APPDATA', '')` 並在啟動時檢查。

### 架構

#### C14. `download_thread` — 2593 行 God Object
- **位置:** `app/core/thread_download.py`
- **描述:** 混合網路、檔案系統、資料庫、過濾、進度回報於單一類別，80+ 方法。
- **修復:** 拆分職責到獨立模組。

#### C15. `get_img_url_thread` — 1833 行 God Object
- **位置:** `app/core/thread_url_fetch.py`
- **描述:** 同上，過大的單一類別。

#### C16. `pixiv_api.py` 混合模組
- **位置:** `app/core/pixiv_api.py` (947 行)
- **描述:** 混合 clean API 函數、Selenium 自動化、遺留獨立腳本、硬編碼 cookie、模組層級副作用 (`option.add_experimental_option("debuggerAddress", ...)` 在 import 時執行)。
- **修復:** 拆分為 `api_client.py`、`selenium_auth.py`、`legacy_scripts.py`。

#### C17. `from pixiv_api import *` 污染全域命名空間
- **位置:** 5 個核心檔案 (`thread_url_fetch.py:11`, `thread_download.py:19`, `thread_pid_scan.py:10`, `pixiv_thread_base.py:9`, `thread_following.py:8`)
- **描述:** Wildcard import 讓每個 thread 模組的命名空間被完全污染，無法確定實際使用了哪些名稱。Selenium 相關的全域變數在每個模組載入時都被引入。
- **修復:** 改為顯式 import 所需名稱。

---

## 🟠 HIGH (20) — 應盡快修復

### 安全

#### H1. 密碼明文存儲
- **位置:** `app/core/settings_store.py:71,261`；`app/gui/views/settings_view.py:49,373`
- **描述:** Pixiv 密碼以明文存於 `%APPDATA%/pixiv_download/settings.json`，無加密、無 OS credential store 整合。
- **修復:** 使用 `keyring` 函式庫或 Windows DPAPI。

#### H2. Cookie/密碼印到 console
- **位置:** `app/core/pixiv_api.py:296,386,422`
- **描述:** 完整 session cookie 和 email 被 `print()` 到 stdout，可能被 log 檔案或 CI 輸出捕獲。
- **修復:** 替換為脫敏 logging，永不印出 cookie 或密碼。

#### H3. 追蹤檔含認證鄰近資料
- **位置:** `app/core/pixiv_api.py:692-713`
- **描述:** `pixiv_cookie_requirement.json` 追蹤檔記錄每個 PID 的 cookie 需求狀態，且使用 `atomic_write_json` 建立歷史備份。
- **修復:** 確保追蹤檔使用 `backup=False`。

#### H4. Chrome debug port 硬編碼
- **位置:** `app/core/pixiv_api.py:275`
- **描述:** `option.add_experimental_option("debuggerAddress", "127.0.0.1:9527")` — 任何本地行程都可連接此 Chrome 實例並提取 cookie。
- **修復:** 使用隨機可用 port 或移除 debug 選項。

### 並發

#### H5. JXL worker `Queue.get()` 無 timeout
- **位置:** `app/core/thread_download.py:1218`
- **描述:** `self._jxl_queue.get()` 無限阻塞。若主執行緒崩潰未推入 `None` sentinel，JXL worker 執行緒永遠掛起。
- **修復:** 加入 `timeout=1.0` 並在 timeout 時檢查 `stop_event`。

#### H6. `Queue.get() for _ in range(qsize())` — TOCTOU
- **位置:** `app/core/thread_download.py:2173`；`app/core/thread_url_fetch.py:1520`
- **描述:** 在 `qsize()` 和 `get()` 之間，其他執行緒可能推入新項目，導致 `get()` 永遠阻塞。
- **修復:** 使用 `get_nowait()` 配合 `queue.Empty` 異常，或 drain 到 sentinel。

#### H7. `Queue.join()` worker 崩潰則永遠阻塞
- **位置:** `app/core/thread_download.py:1264`
- **描述:** 若 JXL worker 在處理項目時崩潰未呼叫 `task_done()`，`join()` 永遠阻塞。
- **修復:** 使用 `join(timeout=...)` 或改用 `concurrent.futures.Future` 模式。

### 品質

#### H8. ~410 處 `except Exception: pass`
- **位置:** 分佈於 `thread_url_fetch.py` (~90)、`thread_download.py` (~60)、`flet_app.py` (~15) 等
- **描述:** 大量靜默吞掉異常，使除錯極其困難。多數是 `try: self._q.put(WorkerEvent(...)) except Exception: pass` 的防禦性 queue put。
- **修復:** 建立 `_safe_put()` / `_safe_emit()` 統一輔助函數，或使用 `contextlib.suppress(Exception)`。

#### H9. 重複的 `_init_metadata_db` / `_emit_metadata_db_stats`
- **位置:** `thread_download.py:2088-2098`、`thread_url_fetch.py:756-766`、`thread_pid_scan.py:55-68`
- **描述:** 三個 thread 類別中近乎相同的 2-3 行 wrapper 複製了 3 次。
- **修復:** 移至 `PauseableThread` 基底類別或建立 mixin。

#### H10. 熱迴圈中未編譯正則表達式
- **位置:** `thread_download.py:1856-1858` (splitID)、`thread_download.py:1362-1369` (_normalize_tag_for_filename)
- **描述:** `re.search(r'\.jpg|\.png|\.gif', file)` 等在迴圈中每次迭代建立新 regex 物件。`splitID()` 遍歷整個下載目錄樹。
- **修復:** 提取為模組層級 `re.compile()` 常數。

#### H11. Wildcard imports
- **位置:** 5 個核心檔案
- **描述:** `from pixiv_api import *` 污染命名空間。
- **修復:** 改為顯式 import。

### 錯誤處理

#### H12. `get_follow_illust` 無重試
- **位置:** `app/core/thread_following.py:68`
- **描述:** 單次嘗試，無重試。暫時性 503 會靜默丟失 100 個作者 ID (從 `safe_json` 回傳 `[]`)。
- **修復:** 加入 3 次重試配指數退避。

#### H13. 初始總數請求無重試
- **位置:** `app/core/thread_following.py:94,102`
- **描述:** 確定 `show_total_num` / `hide_total_num` 的兩個 `requests.get` 是單次嘗試。失敗則 `safe_json` 回傳 0，整個 step 靜默產出零結果。
- **修復:** 同上。

#### H14. `jpg_download` 重試永久錯誤
- **位置:** `app/core/thread_download.py:740-758`
- **描述:** broad `except Exception` 攔截 `ValueError` (永久錯誤) 並重試 5 次，浪費時間。
- **修復:** 永久錯誤不應重試，應直接 raise。

#### H15. Selenium 無頁面載入 timeout
- **位置:** `app/core/pixiv_api.py:285-415`
- **描述:** `driver.get(url)` 無 page-load timeout。掛起的頁面永遠阻塞呼叫執行緒。
- **修復:** 設定 `driver.set_page_load_timeout(30)`。

#### H16. `requests.Session` 建立後從未關閉
- **位置:** `app/core/pixiv_api.py:17-28`；`app/core/thread_download.py:753`
- **描述:** `make_session()` 建立的 session 從未被呼叫者明確關閉。重複建立可能耗盡檔案描述符。
- **修復:** 使用 context manager 或在 `finally` 中呼叫 `session.close()`。

#### H17. `os.mkdir` vs `os.makedirs`
- **位置:** `app/core/thread_download.py:178-179`
- **描述:** `os.mkdir(self.download_path)` — 若父目錄不存在會失敗。
- **修復:** 改為 `os.makedirs(self.download_path, exist_ok=True)`。

### 架構

#### H18. 無依賴注入
- **描述:** 核心邏輯直接依賴檔案系統、網路、`os.getenv`，無法在不連網路/檔案系統的情況下測試。
- **修復:** 引入 injectable HTTP client 和 filesystem abstraction。

#### H19. PHASE-A 遷移未完成
- **描述:** 新舊表並存，JSON/DB 雙寫，遷移工具存在但未完成切換。
- **修復:** 完成 Phase B — 切換讀取端到 DB-only，移除 JSON fallback。

#### H20. `sys.path` 依賴
- **描述:** 根目錄 shim 檔案 + `from pixiv_api import *` 依賴 `sys.path` 含根目錄。測試需 `conftest.py` 手動設定。
- **修復:** 統一使用 `app.core.*` 相對 import。

---

## 🟡 MEDIUM (21)

### 安全

| # | 位置 | 問題 |
|---|------|------|
| M1 | `thread_download.py:1010-1029` | cjxl 路徑來自使用者設定，可指向任意執行檔 |
| M2 | `thread_download.py:1033` | tempfile 在 Windows 上繼承預設 ACL |
| M3 | `thread_pid_scan.py:641-643`, `pixiv_api.py:233-234`, `thread_following.py:478-480` | 多處使用 raw `open()` 而非 atomic write |
| M4 | `settings_store.py:66-78` | 所有認證資料集中於單一明文 JSON |
| M5 | 多處 | bare `except Exception: pass` 吞掉安全相關錯誤 |

### 並發

| # | 位置 | 問題 |
|---|------|------|
| M6 | `thread_download.py:1073-1079` | `_persist_pending_pid_file` 讀取 set 未持鎖 |
| M7 | `thread_download.py:2383` | `pid_now` 從多 worker 非原子遞增 |
| M8 | `thread_following.py:65-66` | 全域 `pid_num` 從 16 個 executor worker 修改 |
| M9 | `thread_following.py:109` | `self.executor` 共用屬性 — `__del__` 與 context manager 競爭 |
| M10 | `safe_io.py:87-93` | fallback `open('w')` 在 Windows 上非原子 |

### 品質

| # | 位置 | 問題 |
|---|------|------|
| M11 | 18 處 | E501 行長違規 (長 HTML 字串) |
| M12 | `pixiv_api.py` (947 行) | 混合 Selenium、HTTP API、遺留 thread 程式碼 |
| M13 | `settings_view.py:30` | `__init__()` 146 行，應提取 widget 建構為方法 |

### 錯誤處理

| # | 位置 | 問題 |
|---|------|------|
| M14 | `pixiv_thread_base.py:325-356` | `_run_with_network_retry` 使用固定 60s 等待，無指數退避 |
| M15 | `pixiv_api.py:787-790` | 429 rate-limit 只重試 1 次，之後回傳 `[404]` |
| M16 | `pixiv_thread_utils.py:471-475` | `append_diagnostic_event` 靜默失敗 |
| M17 | `pixiv_api.py:285-415` | Selenium 硬編碼 debugger address 於 import 時設定 |
| M18 | `thread_following.py:109-121` | Step 1 `executor.map` — 一個頁面失敗整個 run 失敗 |
| M19 | `thread_pid_scan.py:343-361` | Step 2 多執行緒模式無重試、無 scheduler |
| M20 | `pixiv_api.py:610-631` | `_pixiv_info_with_retry` 只重試 2 次無 backoff |
| M21 | `thread_download.py:119` | `os.mkdir` 改 `os.makedirs` |

### 架構

| # | 問題 |
|---|------|
| M22 | `typing.Callable` 應改 `collections.abc.Callable` (3 檔案) |
| M23 | `update_selenium.py:7` — 多個 import 在同一行 |

---

## 🟢 LOW (16)

| # | 維度 | 位置 | 問題 |
|---|------|------|------|
| L1 | 安全 | `pixiv_api.py:585`, `pixiv_thread_base.py:225`, `account_scheduler.py:120` | `random` 模組用於非安全場景 (可接受) |
| L2 | 安全 | `pyproject.toml:6` | 未 pin 依賴版本，供應鏈攻擊風險 |
| L3 | 安全 | `certs.pem` (根目錄) | Bundled CA 可能過時 |
| L4 | 安全 | `pixiv_api.py:453,538` | URL injection — 使用者輸入直接串接 URL，line 538 有多餘 `%27` |
| L5 | 品質 | `app/core/pixiv_api.py:59` | 未使用 import `Path` (vulture 90% confidence) |
| L6 | 品質 | `app/gui/run_actions.py:18` | 未使用 import `backup_file` (ruff F401) |
| L7 | 品質 | `pixiv_api.py:435` | `i=i+1` 迴圈變數 `i` 從未被讀取 |
| L8 | 品質 | `update_selenium.py:25` | `%` 格式化應改 f-string (UP031) |
| L9 | 品質 | `account_scheduler.py:7`, `dispatcher.py:5`, `app_logging.py:18` | `from typing import Callable/Any` 應改 `from collections.abc import Callable` |
| L10 | 並發 | `dispatcher.py:33-43` | EventDispatcher 無 backpressure — 一次 drain 整個 queue |
| L11 | 並發 | `safe_io.py:76-85,111-117` | `os.replace` 在 Windows 上若目標被其他程式開啟會 `PermissionError` |
| L12 | 並發 | SQLite | 多個 `MetadataDB` 實例 → 高寫入負載下可能 `SQLITE_BUSY` |
| L13 | 錯誤 | 多處 | 無 disk-full 處理 |
| L14 | 錯誤 | 多處 | 無 permission-error 處理 |
| L15 | 錯誤 | 多處 | 診斷事件 logging 失敗靜默 |
| L16 | 架構 | — | 無 lock file (pip freeze 輸出) |

---

## 📋 修復優先順序

| 優先級 | 行動 | 項目 | 預估工時 |
|--------|------|------|----------|
| **P0 立即** | 移除硬編碼 cookie + 啟用 SSL 驗證 | C1, C2 | 2h |
| **P0 立即** | 加鎖保護並發共享狀態 | C3, C4, C5, C6 | 4h |
| **P0 立即** | 修 bare `except:` | C8 | 0.5h |
| **P1 本週** | 修 Selenium driver 洩漏 + 加 cjxl timeout | C10, C11 | 2h |
| **P1 本週** | 修 HTTP 錯誤分類 (暫時 vs 永久) | C12 | 2h |
| **P1 本週** | 修 `os.getenv('APPDATA')` 安全性 | C13 | 1h |
| **P1 本週** | 加 retry 到 `get_follow_illust` | H12, H13 | 2h |
| **P2 本月** | 拆分 `thread_download.py` (2803行) 為子模組 | C9, C14 | 8h |
| **P2 本月** | 重構 `main()` (CC=26) | C7 | 4h |
| **P2 本月** | 建立 `_safe_put()` 統一錯誤處理 | H8 | 4h |
| **P2 本月** | 提取重複程式碼為 mixin | H9 | 3h |
| **P2 本月** | 密碼改用 OS keyring | H1 | 4h |
| **P2 本月** | 編譯熱迴圈正則表達式 | H10 | 1h |
| **P3 長期** | 完成 PHASE-A 遷移 (Phase B) | H19 | 8h |
| **P3 長期** | 引入依賴注入改善可測性 | H18 | 16h |
| **P3 長期** | 統一 import 模式 (消除 wildcard) | H11 | 4h |
| **P3 長期** | pin 依賴版本 + 鎖檔 | L2 | 1h |

---

## 📊 工具驗證結果

執行以下命令可獲得定量數據：

```bash
ruff check app/                                          # E/F/UP/B/SIM 規則
radon cc app/ -n C -s                                    # 圈複雜度 >= C
radon mi app/ -n B                                       # 維護性指數 <= B
lizard -C 15 -L 100 app/                                 # 認知複雜度/長函數警告
vulture app/ vulture_whitelist.py --min-confidence 80    # 死碼偵測
pylint --disable=all --enable=duplicate-code --min-similarity-lines=8 --recursive=y app/  # 重複區塊
```

報告時間: 2026-05-27
