---
title: 拆分 >1000 行檔案 + 重構(復用/簡化/性能)— session handoff
created: 2026-06-14
status: in_progress (低風險批次已落地 1/4 檔,其餘待續)
scope: 完整計畫(含高風險),由使用者確認
owner_note: 此檔是給「下一個 session」直接接手用的。讀完就能繼續,不必重跑分析。
---

# 0. 給接手 session 的 TL;DR

把 `app/core/` 下 4 個超過 1000 行的檔案拆成多個 <1000 行的內聚模組,順手提升復用、簡化邏輯、標記性能點。**已完成 `pixiv_thread_utils.py`(1011→750)**;剩 `metadata_db.py`、`thread_download.py`、`thread_url_fetch.py`。

**鐵則(務必遵守,使用者明確要求)**:
1. **一次一個 transformation,每步跑全測綠才繼續**。基線:`python -m pytest -q -m "not integration"` → 目前 **916 passed**。不可降。
2. **純函式 / 純資料**:用「Move Function + re-export shim」——把函式搬到新檔,原檔改成 `from app.core.<new> import (...)  # noqa: F401`,讓所有 `from app.core.pixiv_thread_utils import X` caller **零改動**。
3. **Thread/DB 類別上共享 `self` 狀態的方法群**:不能純搬,要抽 helper class + 依賴注入(把 `self._q`/`self._metadata_db`/`self._scheduler` 等當建構參數傳入)。**較高風險,逐一做**。
4. **不可平行盲拆**(不要派多個 agent 同時改多檔)。重構紀律 > 速度。
5. **GUI/threading/DB 改動「綠測不算數」**:每抽完一塊有實機風險的,**停下來讓使用者重啟 Flet app 跑一輪 combined 驗無回歸**(Flet 不熱載入)。使用者記憶:`feedback_verify_on_real_app`。
6. 不要動 `backup/` 下的死碼副本;根目錄的 `pixiv_thread_utils.py` 等是 shim(`from app.core... import *`),re-export 會自動穿透,不用改。

**環境雷**:這個 repo 有 `GateGuard` hook,每次 Write/Edit/首個 Bash 會擋一次要你「先陳述事實再 retry」。可忍(retry 一次就過),或照提示設 `ECC_GATEGUARD=off` / 把 `pre:edit-write:gateguard-fact-force`、`pre:bash:gateguard-fact-force` 加進 `ECC_DISABLED_HOOKS`(env 要在 Claude Code 主行程,session 內設沒用,得重啟)。

**參考**:完整唯讀分析(5 個 agent)原始輸出在
`<session-tmp>/tasks/w2b5wqhe3.output`(若還在);本檔已把結論濃縮進來。`tasks/todo.md` 有對應的進度區塊;`tasks/lessons.md` 有相關教訓。

---

# 1. 目標檔與現況

| 檔案 | 原行數 | 現行數 | 整體風險 | 狀態 |
|------|--------|--------|----------|------|
| `app/core/pixiv_thread_utils.py` | 1011 | **750** | low | ✅ 已降到 <1000 |
| `app/core/metadata_db.py` | 1448 | **981** | low | ✅ 已降到 <1000 |
| `app/core/thread_download.py` | 3080 | **2598** | medium | 🟡 低風險塊已抽(filename+JXL);剩高風險塊待實機驗 |
| `app/core/thread_url_fetch.py` | 2092 | 2092 | high | ⬜ 待拆 |

已新增:`app/core/pid_utils.py`(104)、`app/core/cookie_utils.py`(215)、
`metadata_db_schema.py`(92)、`metadata_db_cache.py`(48)、
`metadata_db_migration.py`(154)、`metadata_db_artwork.py`(265)、
`step4_filename.py`(210)、`step4_jxl_conversion.py`(337)。

**手法確立(metadata_db 驗證過):公開 API 方法群用 mixin 拆。** DB 方法是 34 處
caller 以 `db.method(...)` 呼叫的公開 API,handoff 原建議的「DI 自由函式」對它行不通
(留 delegation stub=不減行數;改 caller=高風險)。改用 mixin（`class MetadataDB(_MigrationMixin, _ArtworkMixin)`，
方法本體逐字搬進 mixin、`self._conn/_lock/_emit/_coerce_pid/_bulk_write` 由具體類別提供）：
零 caller 改動、真正減行數、行為機械等價。純模組級常數/函式（schema DDL、cache 原語）
仍用 Move+re-export。**注意:re-export import 要放在檔案頂端(任何陳述式之前),否則 ruff E402。**

---

# 2. 已完成(範例樣板,照這個 pattern 做)

## ✅ `pid_utils.py`(從 pixiv_thread_utils 搬出,純 stdlib,零耦合)
搬移:`normalize_pid`、`normalize_pid_set`、`canonicalize_pximg_url_for_storage`、`_extract_pid_candidates_from_name`、`_PID_FROM_NAME_PATTERNS`。
原檔改成 `from app.core.pid_utils import (...)  # noqa: F401`。全測 916 綠。

## ✅ `cookie_utils.py`(同上)
搬移 12 個 cookie pool 函式:`_strip_cookie_prefix`、`_parse_cookie_entry`、`_merge_duplicate_entry`、`_dedupe_cookie_entries`、`_fill_missing_aliases`、`normalize_cookie_entries`、`normalize_cookie_pool`、`cookie_usage_label`、`format_cookie_usage_summary`、`cookie_speed_divisor`、`apply_cookie_pool_speedup`、`init_cookie_fields`。原檔 re-export。全測 916 綠。

> pixiv_thread_utils 剩下的(safe_json/output_err、folder 掃描+mtime 快取、JSON recovery、text I/O wrapper、`fetch_with_cookie_retry`)已 <1000,**先不用再拆**;若之後要,分析建議再切 `folder_cache.py`/`json_recovery.py`/`text_io_utils.py`(全 low,但收益遞減)。`load_exist_pid_set` 系列是 PHASE-B 要刪的,別重構它。

---

# 3. ✅ 完成:`metadata_db.py`(1448 → 981,整體 low)

**已於 2026-06-14 拆完並全測綠(916 passed)、ruff 全綠。** 4 個 transformation,每步驗證:
1. `metadata_db_schema.py`(92):`_SCHEMA` DDL 字串純搬移 + 內部 import。
2. `metadata_db_cache.py`(48):`_db_file_signature`/`_CLOSED_SET_CACHE`/`_CLOSED_SET_CACHE_LOCK` 純搬移 + re-export(test 讀 `mdb._db_file_signature`)。
3. `metadata_db_migration.py`(154):`_MigrationMixin`（`import_meta_dict`/`_build_artwork_row`/`import_downloaded_set`/`export_meta_dict`）。
4. `metadata_db_artwork.py`(265):`_ArtworkMixin`（`upsert_artwork`/`upsert_artworks`/`backfill_user_ids`/`get_artwork`/`user_id_map_for_pids`/`mark_artwork_revoked`/`artwork_count`/`pending_artwork_count`/`get_pending_artwork_pids`）。

`class MetadataDB(_MigrationMixin, _ArtworkMixin)`。`_conn`/`_lock`/`_emit`/`_bulk_write`/`_coerce_pid`/連線管理仍留在 metadata_db.py(基礎設施不動)。**剩餘 pages CRUD 群（~270）未抽**(981 已 <1000,收益遞減);若日後要再降,`metadata_db_pages.py`（`_PagesMixin`）是下一個乾淨候選。

---

## 原始規劃(保留供參考)

DB 是 canonical store,最關鍵;但這支整體風險 low、且有 `test_closed_set_cache.py` 等護網。先抽最自足的。

### 3a. `metadata_db_cache.py`(closed-set 快取,~180,**low,優先**)
- 純模組級可搬:`_db_file_signature`、`_CLOSED_SET_CACHE`、`_CLOSED_SET_CACHE_LOCK`(metadata_db 約 line 149-166)。**這幾個可直接 Move + re-export。**
- 方法 `_compute_closed_artwork_set`、`closed_artwork_set`、`is_pid_complete`、`is_pid_closed`、`complete_artwork_set`(約 1289-1369)用 `self._conn()`,**不能純搬**;選項:(a) 只搬模組級三個、方法留著(收益小但零風險);或 (b) 抽 `_ClosedArtworkSetCache` class,`closed_set(conn, db_path)` 介面,MetadataDB 持有一個實例。建議先做 (a) 試水,再評估 (b)。
- 護網:`tests/test_closed_set_cache.py`。**性能注意**:這是 init 路徑最熱的呼叫(真實 1.26M-row DB 回 ~1.1M PID,每次 Run All 叫 5-6 次),已用 Python 集合組合 + 檔案簽章快取優化過,**不要破壞語意**(見 CLAUDE.md「Startup-cost note」)。

### 3b. `metadata_db_migration.py`(JSON 遷移,~150,low,**未來可整支刪**)
`import_meta_dict`、`import_downloaded_set`、`_build_artwork_row`、`export_meta_dict`(約 469-599)。這是 PHASE-A→B 過渡碼,隔離出來讓「將來可刪」很明顯。依賴 `_bulk_write`/`_coerce_pid`/`_emit` → 抽 class 傳入或留方法。low risk。

### 3c.(可選,medium)artwork / pages CRUD 拆 reader-writer
`metadata_db_artwork.py`(`upsert_artwork`/`upsert_artworks`/`get_artwork`/`user_id_map_for_pids`/`mark_artwork_revoked`/`artwork_count`/`pending_artwork_count`/`get_pending_artwork_pids`,~250)、`metadata_db_pages.py`(`upsert_page`/`mark_page_*`/`upsert_pages_bulk`/`get_page`/`get_pages_for_pid`/`get_pending_pages`/`get_retriable_failed_pages`,~220)。高耦合 `self._conn/_lock/_emit/_coerce_pid` → 用「輕量 writer/reader class 由 MetadataDB 持有」或「模組函式吃 (conn, lock, emit) 參數」。`get_pending_pages` 在熱迴圈,保持零開銷。護網:`tests/test_metadata_db.py`、`test_pending_url_db.py`、`test_metadata_backfill_user_ids.py`、`test_event_log_replay.py`。

### metadata_db 內部去重(順手,low)
- PID 強制+驗證 pattern 在 ~40 處重複 → 抽 `_coerce_and_validate_pid(value, allow_none=False)`。
- datetime 戳記 4 處 → `_now_timestamp(override=None)`。
- JSON tag blob 編解碼 5 處 → `_encode_tags`/`_decode_tags`。
> 這些是「Extract Method → 取代呼叫點」,逐一做、每步綠。

---

# 4. 進行中:`thread_download.py`(3080 → 2598,medium)

**✅ 兩個低風險塊已抽(2026-06-14,全測 916 綠 + ruff 綠)。手法:mixin(與 metadata_db 一致)。**
- `step4_filename.py`(210)→ `_FilenameMixin`:7 個檔名/標籤渲染方法 + 3 個 regex 常數。
  **雷:** `_DECORATIVE_CHARS_RE` 含不可見字元(zero-width / VS selector)+ emoji 用 raw-string `r"\U0001F000-\U0001FAFF"`(交 `re` 解析,非 Python)。抽完用 `re.compile().pattern` byte 比對驗證三個 regex 與原碼完全相同才繼續(`test_normalize_tag_for_filename.py` 30+ 斷言守住)。
- `step4_jxl_conversion.py`(337)→ `_JXLMixin`:21 個 JXL 背景轉檔方法(`_init_jxl_config` + 主區塊)+ `_JXL_SUPPORTED_EXTS`。全 `self.*`(無類名自我引用)→ verbatim 零內部修改。用 Python script 依行號機械抽取(避免手打 CJK log 字串誤差)。搬完移除 thread_download 4 個只剩 JXL 用的 import(glob/subprocess/tempfile/shutil)。
- `download_thread(PauseableThread, _FilenameMixin, _JXLMixin)`。

**⬇️ 以下高風險塊未做——使用者偏好實機驗(GUI/threading/download 綠測不算數)。每塊抽完都要停下讓使用者重啟 Flet 跑一輪 combined 驗無回歸。**

**先做兩個低風險的,把檔案先砍 ~410 行:**

### 4a. `step4_jxl_conversion.py`(~280,low,正交)
JXL 背景轉檔整組:`_init_jxl_config`、`_jxl_should_convert`、`_jxl_run_conversion`、`_jxl_tally_sizes`、`_jxl_emit_*_log`、`_jxl_delete_source_if_configured`、`_jxl_record_outcome`、`_convert_file_to_jxl`、`_start_jxl_worker_if_needed`、`_jxl_worker_loop`、`_enqueue_jxl`、`_discard_pending_jxl_items`、`_drain_jxl_queue`、`_handle_existing_jxl_destination`、`_warn_cjxl_missing_once`、`_resolve_cjxl_path`、`_build_jxl_command`、`_run_cjxl_once`、`_run_cjxl_with_temp_ascii_path` + `_JXL_SUPPORTED_EXTS`。
- 它是獨立背景 thread + FIFO queue,**不碰 URL/PID/account 狀態**。抽成 `JXLConverter(enable, cjxl_path, effort, delete_orig, q, stats_collector)` class,介面 `enqueue(path)` / `drain(discard=False)` / `get_stats()`。download_thread 持有一個實例。
- 讀:`self.jxl_*` flags、`self._stats_collector`(optional)、`self._q`;寫:`self._jxl_*` 計數/thread/queue → 都搬進 class。
- 護網:`tests/test_jxl_fallback.py`、`test_jxl_outcome_helpers.py`。**保持「無 cjxl.exe 不可壞下載」**。

### 4b. `step4_filename.py`(~130,low,純)
`_normalize_tag_for_filename`、`_build_hashtag_text`、`_split_timetag`、`_filename_template_fields`、`_render_template_filename`、`_render_default_filename`、`_build_download_filename` + regex 常數(`_ZERO_WIDTH_RE` 等)。
- 目前讀 `self.notag`/`self.notime`/`self.filename_template`/`self.tag_strip_brackets`/`self.tag_strip_special_chars` → **改成函式參數**(無狀態)。
- 護網:`tests/test_normalize_tag_for_filename.py`、`test_build_download_filename.py`。

**再做高風險的(每個都停下實機驗):**

### 4c. `step4_url_filter.py`(~350,medium)
filter 決策 + task 準備:`_passes_pid_filter`、`_fetch_meta_for_filter`、`_fetch_filter_meta_via_scheduler/_direct`、`_prepare_download_tasks`、`_classify_url_for_filter`、skip 計數那組等。抽 `URLFilter(config, db, url_meta, scheduler)`,`filter_pid(pid, allow_network)`/`prepare_tasks(urls, allow_network)`,logging 走 callback。讀寫 `self.url_meta`/`self._pid_filter_decision` → DI。

### 4d. `step4_ugoira.py`(~220,medium)
ugoira GIF:`_normalize_ugoira_frames`、`_save_ugoira_gif`、`_stream_ugoira_zip_bytes`、`_extract_ugoira_frame_blobs`、`_fetch_ugoira_meta` 等。緊耦合 `self.timelock`(download_time 原子推進)、`self.url_meta` 寫入 → 傳入。護網:`test_gif_download_helpers.py`、`test_ugoira_meta_helpers.py`。

### 4e. `step4_download_execute.py`(~380,**high,最後**)
pool/single + author-batch 編排:`_execute_downloads*`、`_download_pid_group`、`_download_pid_with_scheduler`、`_resolve_execution_order`、`_group_urls_by_pid` 等。緊耦合 scheduler + stop/pause event + 可變狀態(exist_pid/url_meta)。風險最高,前面都穩了再碰。護網:`test_execute_downloads_pool_author_barrier.py`、`test_reorder_pid_order_by_author.py`。

### thread_download 內部去重(順手)
PID-from-filename 三個解析 merge 成一個;`_build_artwork_headers` 兩處呼叫包成 `_build_pid_download_headers`;timelock 推進包成一個 helper;filter-skip 計數抽 `FilterSkipTracker`。

---

# 5. 待辦:`thread_url_fetch.py`(2092,**整體 high,最後做**)

Step 3 查詢引擎,跟 orchestration 綁很緊。建議順序:**先 `step3_filters.py`(medium)驗證手法**,再 I/O,其餘最後。

- `step3_filters.py`(~300,medium):`_normalize_artwork_tags`、`_tag_hit`、`_step3_blocked_by_ban_tag`、`_step3_missing_must_tag`、`_step3_below_like_threshold`、`_passes_artwork_filters` + skip 計數那組 + `_mark_revoked_pid`/`_flush_revoked_pid_file`。抽 `FilterTracker`,DI `self._q`/`self.path`/`self._metadata_db`。護網:`test_step3_url_helpers.py`、`test_get_download_url_helpers.py`。
- `step3_cookie_requirement.py`(~250,medium):`pixiv_cookie_requirement.json` 的 schema/migration 那 15 個方法(`_cookie_requirement_*`、`_migrate_url_meta_schema`、`_set_requires_cookie_meta`、`_refresh_cookie_requirement`)。
- `step3_io.py`(~700,high):file I/O / pictures_id 載入 / all_url 快照 / pending-PID 追蹤 / finalize 寫檔。極高耦合 `self.path`/`self._metadata_db`/`self.exist_pid`/`self._stop_event`/queues → `Step3IO` class DI。
- `step3_cache.py`(~500,high):metadata JSON↔DB、cookie-requirement、fresh/usable 判斷、prefilter。雙向寫 `url_meta` + DB。
- `step3_query.py`(~450,high):網路查詢編排(cache→network→404→URL 展開→stamp→filter)+ `get_download_url`。最緊耦合,**最後**。

**重要**:combined 模式(`thread_combined.py`)**組合**了 fetcher(`get_img_url_thread`)當 helper,任何對 `get_download_url` / `_step3_*` / pending-PID tracker / progress emit 的改動都要確認 combined 不回歸(見 lessons.md 的 `_DropOverallProgressQueue` 那條)。護網:`tests/test_combined_*.py` 全跑。

---

# 6. 跨檔復用(分析另列;**等上面單檔都拆穩再做**)

- `pixiv_thread_base.py` 集中:`_init_cookie_fields()`(包 `init_cookie_fields` 一次設 4 個屬性)、高階事件 helper(`_emit_progress`/`_emit_countdown`/`_emit_error` 取代散落的 `self._q.put(WorkerEvent(...))`,約省 80 行)、`_init_pid_cookie_selection()`。風險 low,但動到所有 worker 的 __init__,要每支跑測。
- `metadata_db.py` 包一個 init+emit_stats+mirror 的 factory,4 支 worker 的 DB 初始化共用(分析估省 ~40 行)。
- 統一 import 路徑:確認 `thread_url_fetch`/`thread_download` 不要各自重實作 `_normalize_filter_tags`/`_resolve_like_threshold`,都 import `pixiv_thread_base`。

---

# 7. 每一步的驗證協議(照做)

1. 改前:`git status` 乾淨度心裡有數(目前 working tree 已有未 commit 的 cookie 修復 + 前兩個抽取,**使用者尚未要求 commit,別自己 commit**)。
2. 抽一塊 → `python -m pytest -q -m "not integration"` 必須 **≥916 passed**。
3. 碰到 scheduler/stop/url_meta/exist_pid/DB 連線/GUI 的高風險塊:跑完測試後**停下,請使用者重啟 app 跑一輪 combined**(下載成功、cookies 頁時間前進、整體進度正常、檔名遞增)再繼續下一塊。
4. 更新 `tasks/todo.md` 對應勾選 + 本檔狀態。
5. 全部做完且使用者實機 OK 後,才考慮 commit(且依使用者記憶 `feedback_no_claude_coauthor`:**commit 不要加 Co-Authored-By: Claude**)。

# 8. 別碰 / 注意
- `backup/dead_root_dupes/*`:死碼,無視。
- 根目錄 shim(`main.py`/`user_info.py`/`update_selenium.py`/`pixiv_thread_utils.py` 等):re-export 會穿透,不用改。
- `load_exist_pid_set` 及其 helper:PHASE-B 要刪,**別花時間重構**。
- closed-set 快取的 Python 集合組合 + 檔案簽章:**性能關鍵,別改語意**。
- 真實資料規模:~1.1M closed PID / ~204k 檔,性能評估要照這個尺度(使用者記憶 `project_real_dataset_scale`)。
