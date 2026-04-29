# Findings

## Cookie 重複位置確認

### Module-level in pixiv_thread.py
- `_normalize_cookie_entries` line 44–78
- `_normalize_cookie_pool` line 81–82 (呼叫上面那個)
- `_cookie_usage_label` line 85–101
- `_format_cookie_usage_summary` line 104–125

### Per-class cookie methods (重複 2–3 次)
| 方法 | 位置 |
|---|---|
| `_cookie_speed_divisor` | get_pixiv_author_imgID_Thread:420, get_img_url_thread:1462, download_thread:3056 |
| `_apply_cookie_pool_speedup` | get_pixiv_author_imgID_Thread:433, get_img_url_thread:1475 |
| `_cookie_alias_for_value` | get_img_url_thread:1487（等同 module-level `_cookie_usage_label`）|

### __init__ cookie 初始化 (3 處相同的 4 行)
```python
self.cookie_entries = _normalize_cookie_entries(cookies)
self.cookie_pool = [x.get("cookie","") for x in self.cookie_entries ...]
self._cookie_alias_map = {str(x.get("cookie",""))...: ...}
self.cookies = self.cookie_pool[0] if self.cookie_pool else str(cookies or "").strip()
```
出現在：
- get_pixiv_author_imgID_Thread.__init__ line 348
- get_img_url_thread.__init__ line 933
- download_thread.__init__ line 2579

### GUI 層重複
- `user_info.py:488` `_normalize_cookie_entries` instance method（改用 utils 版本即可）
- `controller.py:103` `_normalize_cookie_pool` instance method（改用 utils 版本即可）

## pause/resume/stop 重複位置

| class | pause | resume | stop | flush hook? |
|---|---|---|---|---|
| get_following | 235 | 240 | 244 | _flush_following_snapshot |
| get_pixiv_author_imgID_Thread | 389 | 393 | 397 | 無 |
| get_img_url_thread | 1775 | 1780 | 1784 | _flush_url_meta_snapshot |
| download_thread | 4461 | 4465 | 4469 | stop() 邏輯複雜，需 override |
| test_thread | 4506 | 4510 | 無 | QWaitCondition 不同，不納入 |

## _sleep_with_countdown 位置
- `get_pixiv_author_imgID_Thread:401` — 完整實作
- `get_img_url_thread:1424` — 叫 `_sleep_ultra_slow`，logic 略有不同（有 cache_hit 判斷、nocookie 分支）
  → 只把 get_pixiv_author_imgID_Thread 的移到 base class，get_img_url_thread 的保留（邏輯不同）

## pixiv_thread_utils.py 現有 imports
`datetime, json, os, re, shutil, sys, traceback`
→ 加入 cookie functions 不需額外 import

---

## Session 7 (2026-04-29) — UI bug 調查（agent 回報）

**Bug：** Frameless 視窗下方進度顯示區（`progressBar` / `output`）沒被白底容納，progressBar 文字看不見。

### 根因（信心高）
1. `QProgressBar` **完全沒在 stylesheet 內**（`controller.py:670` 的 selector list 沒列），加上 `WA_TranslucentBackground`（line 640）+ `QMainWindow{background:transparent}`（line 663），native ProgressBar 繪製出問題 → 透明 + 文字看不見
2. `QTabBar` / `QSplitter::handle` / `QStatusBar` 也都沒設背景，會穿透到透明 MainWindow
3. `centralwidget` 掛 `QGraphicsDropShadowEffect(BlurRadius=36)`（line 653-657），但 `verticalLayout_4` margins 只 10px，陰影外擴 36px 被 framelesswindow 裁切
4. effect 套在 centralwidget 會影響所有子孫 painter——QTextBrowser/ProgressBar 在 translucent 環境下異常的常見元兇

### 修法選項（小→大）
- **單行修法（推薦先試）**：刪 `controller.py:640` `self.setAttribute(Qt.WA_TranslucentBackground, True)`。`qframelesswindow.FramelessMainWindow` 自己處理視窗形狀，不需要手動 translucent
- **次小改動**：補 `QProgressBar` / `QProgressBar::chunk` / `QTabBar::tab` / `QSplitter::handle` / `QStatusBar` stylesheet（`controller.py:670` 區段擴充）
- **若仍異常**：移除 `QGraphicsDropShadowEffect`（line 653）或把 margins 從 10 拉到 24-36（`test.ui:25-36`）

### 待確認
- 使用者描述的「進度顯示區」是 `progressBar`（`test.ui:971`）還是 `output`（`test.ui:997`）。output 已有白底規則，progressBar 沒有──最可能是 progressBar
- 是否真的「白字」，還是只是「沒有對比白底所以看不見」

---

## Session 7 (2026-04-29) — Step 3 性能 bug 調查（agent 回報）

**Bug：** Step 3 越跑越慢——agent 找出 O(N²) 結構性問題。

### 根因 1（信心高，主元兇）— `_persist_pending_pid_file` 每 PID 重寫整個 pictures_id.txt
- `thread_url_fetch.py:1214` `_run_processing_loop` 對**每個 PID** 呼叫 `_mark_pid_processed`
- `_mark_pid_processed:989` → `_persist_pending_pid_file:956`
- 內部：`sorted(self._pending_pid_remaining, key=int)` + `atomic_write_text(backup=True)`
- `backup=True` 走 `safe_io.backup_file` → `shutil.copy2` 整檔到 `history/`
- **量級**：總 I/O = O(N²/2)、CPU sort = O(N² log N)
- N=10000 估算：300+ 秒純磁碟開銷，第 1 PID ~5ms、第 10000 PID ~50ms 線性退化

### 根因 2（中信心）— 每 100 PID 的 batch flush 也是 O(n²)
- `thread_url_fetch.py:789` `_write_all_url_snapshot`：每次 flush 重讀整個 `all_url.txt` + canonicalize × N 次 + atomic_write_text(backup=True)
- 量級比根因 1 小 N 倍（頻率 1/100），但 url_meta dict 大時（5MB JSON × 100 次 flush）= 10-30 秒額外

### 根因 3（場景相關）— `_mark_gif_cookie_usage` 寫整個 url_meta JSON
- `thread_url_fetch.py:717` 在 GIF cookie 路徑每個 PID 都呼叫，帶 `backup=True`
- 抓 GIF 多時會疊加根因 1

### 排除
- `all_url_meta.json` 不是元兇（step3 主路徑只更新 in-memory，不寫檔）
- `safe_io.backup_file` 的 max_history=10 邏輯正確
- 沒有 ThreadPoolExecutor 累積、沒有 Selenium leak
- QTextEdit 已 setMaximumBlockCount(2000)

### 修法（小→大）
- **A. 批次化 `_persist_pending_pid_file`**：移到 `_run_processing_loop:1219` batch flush 區塊內，每 100 PID 才寫一次。**100x 加速**
- **B. `pictures_id.txt` 改 `backup=False`**：runtime pending 檔不需要 history。再省 30-50%
- **C. `_write_all_url_snapshot` 也改 `backup=False`**（runtime 中），`_finalize` 才備份
- **D. （結構性）pending PID 改 append-only delta log**：每 PID `atomic_append_text` 一行，O(N) 線性
- **E. （結構性）url_meta 改 jsonl append-only**：避免每次 batch flush 全量序列化
- **F. `_diag` events 改 buffer batch flush**

### 驗證方式
- 加 `time.perf_counter` 包 `_persist_pending_pid_file` 與 batch flush，emit 到 `_diag`
- 跑 500 PID 看單次成本是否從 ~5ms → ~50ms 線性增長
- Process Monitor 監看 `pictures_id.txt` 的 WriteFile 次數是否 >> PID 數

### 不確定
- 實機 `history/` 是否真的限在 10 份（理論正確，未實測）
- Windows `os.replace` 對防毒鎖檔的 stall 也可能是常數因子退化的元兇之一
