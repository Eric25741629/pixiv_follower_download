# 效能優化報告 (feat/perf-optimization)

以效能工程師角度對全 codebase 做平行稽核(6 子系統)→ 驗證 → 只落地「行為保留 + 可量測」的 win,其餘列建議。基準:`pytest` 954→968 passed / 1 skipped(+新測試),零失敗。

## 已落地(3 個,皆帶測試 + 合成微基準)

### 1. GUI:每事件 UI trace 改 opt-in(`9a4d5bc`)
- **瓶頸**:`diag_log.configure()` 從未被呼叫 → UI 事件 trace 永遠開。`EventDispatcher._poll_once` 對**每個** `WorkerEvent` 建 f-string + 跑 `summary()`(對 output 事件做 HTML-strip regex)+ 在 asyncio UI 執行緒同步寫 RotatingFile — 整個 1.1M-PID run 持續發生。
- **修法**:`diagnostics.verbose_logs`(預設 False)閘門;`dispatcher` 以 `ui_trace_enabled()` 守衛,關閉時連 f-string/regex 都不付。worker/download 通道維持開(per-PID,除錯關鍵)。
- **影響**:整個 run 移除 UI 執行緒上每事件的 regex + 同步寫檔。fires 在所有模式。

### 2. 下載:`pixiv_cookie_requirement.json` 程序內快取(`c9ad724`)— **combined 模式最高實用 win**
- **瓶頸**:`get_pixiv_cookie_requirement(pid)` 每次呼叫 `safe_read_json` **整個檔**;在 `_resolve_pid_and_cookie` 中當 meta `requires_cookie` 為 None(常見)時**每頁**呼叫,重試時更多次。
- **修法**:程序內快取,以 `(size, st_mtime_ns)` 檔案簽章為鍵(完全沿用 `closed_artwork_set` 快取模式),簽章變才重讀。公開介面/回傳語意不變。
- **微基準**:400 entry 檔、500 次呼叫 → 讀檔 500 次降為 **1 次**,102.45ms → 30.43ms。**fires 在 combined 下載腳**。

### 3. Step 3:url_meta delta-flush 取代整字典重匯入(`bf4b8a0`)
- **瓶頸**:`self.url_meta` 只增不減;每 25-PID 批次 flush 與每個 GIF PID 都 `import_meta_dict` 整字典 → `sum(25,50,…,N)` = **O(N²/50)** 列匯入。
- **修法**:沿用既有 `_flushed_urls` delta 守衛 → 新增 `_flushed_meta_pids`;批次只匯入新增 delta;per-GIF 只匯入單筆;終端 flush 維持整字典 backstop。`import_meta_dict` 為 ON CONFLICT DO UPDATE/COALESCE → 終端 backstop 重寫不產生差異 → **持久化結果位元組相同**。
- **微基準**:N=500、20 次 flush → 列匯入 5250 → **500**(O(N²)→O(N)),1.1M 規模降數萬倍。**僅 classic Step 3**(combined 走 `_persist_pid_meta` 逐 PID,不經此路徑)。

## 建議(未盲落地 — 邊際效益低或需你真實資料 profiling)

### 4. Step 4:把 meta 解析 hoist 出重試迴圈 — **部分可行,邊際**
- `_jpg_attempt` 每次重試重跑 `_resolve_pid_and_cookie` + `_load_artwork_metadata`。但 `_resolve_pid_and_cookie` 含**副作用** `_record_cookie_usage`(須維持每次重試),不能整段 memoize。可安全 hoist 的僅 `_load_artwork_metadata`(純讀);而 combined 模式 url_meta 是熱的(dict HIT,非 SELECT/HTTP),省下的只是每次重試一個 dict lookup → **邊際**。中風險。建議:profiling 確認 meta 解析確實是熱點再做。

### 5. Folder cache 瘦身 — **需你真實 204k 資料夾 profiling**
- `folder_file_count_cache.json` 內嵌完整 ~1.1M pids list(~11MB),每次 run `safe_read_json` 全讀只為取 `dir_mtimes`;`backup=True` 留最多 10 份 history(~110MB)。pids list 只在 cache MISS 算 `new_pids` 用。
- **修法**:從 cache 移除 `pids`,MISS 時用 `new_pids = scanned_pids - disk_set`(disk_set 已載入)。11MB → sub-MB。中風險(改 delta 來源,依賴 PHASE-A shadow-write 的 disk_set 為忠實 superset)。**落地前請在真實資料夾驗證**。

## 已最佳化(稽核確認,無需動)
- `closed_artwork_set` 已 process-cache + Python 組合(CLAUDE.md 記載);`downloaded_set()` 已走此快取。
- Step 3 `all_url` 寫入已有 `_flushed_urls` delta 守衛。
- 其餘候選(DB N+1 author-dir、dual-SAVEPOINT、partial revoked index、JXL stat 等)經評估為個位數 µs / 不在關鍵路徑,不值得動(避免過度工程)。

## 給使用者(combined 主工作流)
實用優先序:**#2(cookie 快取,已落地)> #1(UI trace,已落地)> #5(folder,需你 profiling)> #4(邊際)**。#3 對你不 fire(classic Step 3 才有)。
