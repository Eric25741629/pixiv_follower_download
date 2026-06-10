---
goal: download.author_order 開啟時，combined mode 下載順序 + 步驟2 pictures_id.txt 實體順序都「同作者連續」
started: 2026-06-07
status: in_progress
spec: docs/superpowers/specs/2026-06-07-combined-author-order-design.md
---

## [2026-06-10] 跳到最新重構(意圖驅動狀態機)+ log 跨行框選複製

spec: docs/superpowers/specs/2026-06-10-log-follow-rearchitecture-design.md

- [ ] 先寫測試 tests/test_log_panel_follow_state.py(紅):滾輪上→關跟隨;END 貼底→開;END 離底→關;END 離底但 _scroll_pending→不變;膠囊→開+捲動;append 裁切 span 區段正確;膠囊可見性恆= not following
- [ ] 新增 app/gui/log_panel.py:LogPanel(單一 selectable Text + ListView 容器 + GestureDetector 滾輪 + 膠囊 + 狀態機)
- [ ] main_view.py:刪除舊 log 欄位/方法(_log_lines、_auto_scroll_enabled、_last_scroll_pixels、_last_max_scroll_extent、_on_log_scroll、像素差魔術數字),append_log 轉呼叫 LogPanel;build() 換用 panel 控件,移除無效 SelectionArea
- [ ] 轉綠 + 全測試套件 + ruff 改動檔零新增違規
- [ ] 實機 python main.py 驗證:滾輪上滾出膠囊、滾回底自動恢復、膠囊點擊跳底、跨行框選 Ctrl+C、GestureDetector 不擋滾動
- [ ] codex skill 對抗式 code review

Review:

## [2026-06-09] 整體進度條在 combined「進入下一個 PID」時消失(真根因修復)

問題(使用者第三/四次回報,視窗全程開著):整體進度+ETA 只在「PID 完成、下一個 PID 還沒進 p0」時短暫出現,一進入下一個 PID 的 p0 就消失;本作分頁+log 全程正常。

真根因(前三輪都抓錯):combined 重用 fetcher 的 `get_download_url` → `_step3_finish_pid` → `_step3_advance_progress`,每個 PID 送 `progress(1, fetcher.pid_max)`;combined 從不呼叫 fetcher.run()/`_load_and_filter_pid_list`,所以 `fetcher.pid_max` 永遠 = 0(class 預設)→ 送 `progress(1, 0)` → `update_progress` total<=0 → 整體進度被清空。combined 自己 run() 迴圈送的 `progress(1, len(order))`(total 正確)只在 PID 完成時閃一下。downloader 的 progress 早被 `_CombinedPageProgressQueue` 攔成 page_progress,fetcher 的沒攔。

修法:
- [x] `thread_combined.py`:新增 `_DropOverallProgressQueue`(丟 type=="progress"、其餘照過),`_process_one_pid` 查詢期間 swap `self.fetcher._q`(try/finally 還原)→ combined.run() 成為整體進度唯一發送者。
- [x] `main_view.py`:把兩條進度列統一成 `_make_progress_row`(同構,皆 visible=False 起始)+ `_render_progress_row`(visible-gated reveal/hide),消除「兩條 bar 用不同方式建立」的缺陷(使用者明確點名)。
- [x] 測試:`tests/test_combined_overall_progress_kept.py`(wrapper 丟 progress + 查詢期間不污染整體進度)、`tests/test_main_view_progress_render.py`(+4:兩條同構、reveal-on-update、total<=0 隱藏、reattach 還原)。
- [x] 全測試 836 passed;ruff 對改動檔零「新增」違規(main_view 既有 13 SIM105 不在本次範圍)。
- [x] 已交 codex 交叉驗證(對抗式 review)。
- [x] lessons.md:修正前一輪「Row reflow」誤判,新增「追事件來源/重用 run()-thread 的未初始化計數器」教訓。

## [2026-06-09] 進度條修復(邊查邊下 3+4)

問題(使用者回報):
- 整體進度的「總量 / 現在第幾張」與「預計剩餘」在執行中不顯示,按下「結束」後才整批出現(116/XXX)。
- 進度條太細不好看;兩條進度條沒對齊。

根因:
- `update_progress` / `update_countdown` 只更新子控制(`_progress_text` 等),從不更新外層 Row;`progress_row` 只是 `build()` 區域變數。含 expand ProgressBar 的 Row 中,子控制 `.value` patch 不會讓文字重排 → 文字停在空白,直到按 stop 觸發 loading dialog 的整頁 `page.update()` 才整頁重排(故「自己出現」)。對照 `_page_progress_row` 有呼叫 `row.update()` 所以一直正常。
- 兩條 row 的 lead 縮排(0 vs 24)與 trailing 文字寬度(456 vs 188)不同 → bar 起訖點不一致,沒對齊。
- ProgressBar 用預設高度(~4px 細線)。

修法(只動 GUI 層 `app/gui/views/main_view.py`):
- [x] 先寫測試 tests/test_main_view_progress_render.py(紅 6/7)
- [x] 將 `progress_row` 升為 `self._progress_row`,新增 `self._meta_row`(ETA+倒數);`_safe_update` 改更新外層 Row
- [x] 兩條 bar 共用 LEAD(84)/TRAIL(210)/spacing(12) 常數 → 對齊;`bar_height=12`+圓角6+配色
- [x] `build()` 重繪一次 `_paint_progress()`(reattach 後立即顯示)
- [x] 轉綠 + 全測試(828 passed)+ ruff(新碼零 SIM105,既有 17→13)

Review:
- 根因是 Row 未被 `.update()`(只更新子控制),與「granularity / 第一個 PID 慢」無關 — 使用者糾正後才定位正確。
- 只動 `main_view.py`(GUI 層),未改 combined 進度語意(per-PID),不影響既有測試。
- 已記入 tasks/lessons.md。

## [2026-06-09] 第二輪回報(同一進度條)
- [x] PID/數字太靠右看不到 → 尾欄文字改靠左對齊,緊貼進度條(`TextAlign.LEFT`)
- [x] 「整體進度看不到總進度、按中止才出現」→ 提醒使用者**重啟 app**(Flet 不熱載入);row.update 修正在邏輯上已足(對照 page 進度後續更新也靠 row.update 即時刷新)
- [x] **中止 mid-PID 不續傳(功能 bug)**:`_download_pid_group` 中止後 `failed=[]`,combined 誤判全成功 → 標記完成+關閉+移出 pending。修法:`_process_one_pid` 下載後加 `elif self._stop_event.is_set(): download_ok=False`,保留 pending 列與 pictures_id.txt → 下次續傳。測試 tests/test_combined_stop_resume.py(2)。
- [x] 全測試 830 passed;ruff 無新增違規。

## 根因
- combined mode (`thread_combined.py`) 從不呼叫 `downloader.run()`，所以步驟4 的 `compute_author_order` 整段被跳過 → author_order 是死參數，下載順序 = pictures_id.txt 亂序。
- 步驟2 多執行緒 + append-only 累加 → pictures_id.txt 本來就不依作者分組。

## Change A — combined mode 依作者重排
- [ ] 測試 tests/test_combined_author_order.py（先紅）
- [ ] combined_thread.__init__ 存 self.author_order
- [ ] 新增 _resolve_combined_order（off=現狀逐字相同；on=合併去重+compute_author_order，needs_query 對應）
- [ ] run() 改用 _resolve_combined_order
- [ ] 轉綠

## Change B — 步驟2 實體重排 pictures_id.txt（最終一次，增量維持 append-only）
- [ ] 測試 tests/test_step2_regroup_pictures_id.py（先紅）
- [ ] __init__ 新增 keyword-only author_order=False → self.author_order
- [ ] 新增 _regroup_pictures_id_by_author（讀檔→uid_map→compute_author_order→atomic_write_text backup=True）
- [ ] _commit_step2_outputs 最終呼叫
- [ ] 轉綠

## 接線 + 文件
- [ ] run_actions._build_step2 從 store 讀 download.author_order 傳入
- [ ] 測試 tests/test_step2_author_order_wiring.py（先紅）→ 轉綠
- [ ] 更新 CLAUDE.md combined / author-order 段落

## 驗證
- [ ] pytest -m "not integration" 全綠（無回歸）
- [ ] Ultracode workflow 對抗式驗證
- [ ] 回填 Review

## Review

完成於 2026-06-07。

**根因**：combined mode (`thread_combined.py`) 從不呼叫 `downloader.run()`，所以步驟4 的 `compute_author_order` 被整段跳過 → `author_order` 在 combined mode 是死參數，下載順序 = `pictures_id.txt` 亂序。

**改動**
- `app/core/thread_combined.py`：`__init__` 存 `self.author_order`；新增 `_resolve_combined_order`（off=逐字相同；on=兩批合併、`normalize_pid` 去重 query 優先、`compute_author_order` 分組、作者不明排最後）；`run()` 一次性解析 order 並由它推導進度分母。
- `app/core/thread_pid_scan.py`：`__init__` 新增 keyword-only `author_order`；新增 `_regroup_pictures_id_by_author`（最終一次依作者重排 `pictures_id.txt`，`atomic_write_text backup=True`；增量 flush 仍 append-only）；`_commit_step2_outputs` 收尾呼叫。
- `app/gui/run_actions.py`：`_build_step2(... dl ...)` 從 `download.author_order` 傳入；`_build_thread(2)` 帶 `dl`。
- `CLAUDE.md`：更新 Author-ordered / Combined 段落。

**驗證**
- 新增 13 測試（`test_combined_author_order.py` 6、`test_step2_regroup_pictures_id.py` 5、`test_step2_author_order_wiring.py` 2）：先紅後綠。
- 全 `-m "not integration" -p no:randomly`：768 passed。
- ruff：3 個改動檔零新增錯誤（`thread_pid_scan.py` 既有 18 個 silent-failure idiom 未動，符合最小變更）。
- Ultracode 對抗式 workflow（9 agents）：5 findings、0 confirmed 為真缺陷；採納其中 1 個 robustness 清理（進度分母一次解析）。

**使用者操作**：設定打開「依作者順序下載（同作者連續）」(`download.author_order`)，重跑步驟2（回填舊 PID 作者），之後 combined / 步驟4 下載即同作者連續。

**未做（spec 非目標）**：combined 不把分組順序寫回 `pictures_id.txt`（只控下載順序，檔案重排由步驟2 負責）；不改步驟3/4 既有 author_order 行為。

---

## 追加：步驟2 補齊全畫師 user_id（2026-06-07）

**動機**：使用者既有 87,607 筆 pictures_id，多數舊 PID 的 `user_id` 為 NULL → 第一次重排大多落入「作者不明」桶，分組效果有限。

**改動**
- `app/core/metadata_db.py`：新增 `backfill_user_ids(pids, user_id)` —— UPDATE-only（`WHERE user_id IS NULL OR ''`），first-writer-wins，**絕不 INSERT**（不會新增 v_pending_artworks 列、不擾動截斷/佇列）；emit `artwork.user_id_backfill` 供 replay。
- `app/core/event_log.py`：`_dispatch_table` 註冊 `artwork.user_id_backfill` replay handler。
- `app/core/thread_pid_scan.py`：新增 `_step2_backfill_author_user_ids`（gated author_order、`_step2_db_write_lock` 序列化、吞例外）；`thread_no_use_seleium_get_pid` 對每位畫師的**全清單（kept + 被截斷的 skipped）**呼叫它；`_init_step2_run_state` 加 `_step2_db_write_lock`。
- `CLAUDE.md`：更新 author-order 段 + event kinds。

**效果**：重跑步驟2 即可把每位畫師的全部（含被增量截斷的舊）PID 補上作者，之後重排/combined/步驟4 都能正確依作者分組，不必逐筆重查。截斷邏輯本身維持不變（使用者決定）。

**驗證**：新增 10 測試（`test_metadata_backfill_user_ids.py` 6、`test_step2_user_id_backfill.py` 4）先紅後綠；全套件 **778 passed**；ruff 改動檔零新增錯誤（metadata_db 乾淨、thread_pid_scan 維持既有 18、event_log 既有 7）。

---

## 追加2：步驟2 強制重新掃描（忽略30天）+ commit（2026-06-07）

**動機**：使用者剛用舊碼掃過的 37 個畫家已進 30 天跳過窗，新 backfill 不會自動套到他們。需要一次性強制重掃。

- [ ] DEFAULTS download 加 `force_full_rescan: False`
- [ ] thread_pid_scan `__init__(force_rescan=False)` → self.force_rescan
- [ ] `_filter_work_list` 在 force_rescan 時忽略 30 天、全掃（emit 提示）
- [ ] `_step2_backfill_author_user_ids` gate 改 `author_order or force_rescan`
- [ ] `run_actions._build_step2` 讀 `download.force_full_rescan` 或 `self.force_rescan`(CLI)，傳入；GUI 旗標讀後 consume(寫回 False，update_fields)
- [ ] settings_view 加一次性 Switch（autosave 清單；不進 save() 明列鍵 → consume 不被回寫）
- [ ] CLI `run --step 2 --force-rescan`（headless 設 controller.force_rescan）
- [ ] 測試 + 全綠 + ruff
- [ ] commit 全部（author-order + user_id backfill + force-rescan；不加 Claude co-author）

---

## 追加3：步驟 2/3/4 初始化加速（2026-06-07）

**動機**：使用者回報步驟 2/3/4 啟動前初始化耗時很久。實測真實資料（DB 1.26M 列、下載夾 204,536 檔）找出熱點。

**根因（實測）**
- `closed_artwork_set()`（`v_closed_artworks` 三路 UNION 丟 1.1M 列進 TEMP B-TREE 去重）= **22.9s**，每次 Run All 被呼叫 5-6 次（`_build_step2/3`、`_build_combined`、folder-sync 的 `_augment_exist_pid_from_db`、`download_thread._load_initial_exist_pid_set`、`emit_db_stats` 的 `downloaded_count`）。
- 下載夾每次 `os.walk` 兩趟（count + scan）= **~24s**。
- `mirror_exist_pid_set` 把剛從 DB 讀出的 1.095M closed set 又 INSERT 回去 + 寫巨大 event log 行。
- 步驟3 `_migrate_url_meta_schema` 在 url_meta 為空時仍解析 11×82MB cookie_requirement（primary + 10 history）= **~10s**。

**改動（全部 root-cause、零行為回歸）**
- `app/core/metadata_db.py`：`closed_artwork_set` 改 Python 集合組合 `(sentinels−pending)|complete|revoked`（結果逐一驗證與舊 view 一致）+ process 全域快取（key=DB 檔 `size+mtime_ns`，含 -wal，寫入自動失效）；`downloaded_count`→`len(closed_artwork_set())`；新增 `_db_file_signature`、`_compute_closed_artwork_set`。
- `app/core/pixiv_thread_utils.py`：`normalize_pid` 純數字快速路徑；新增 `_scan_download_folder`（單趟 walk 回傳 pids+dir_mtimes+count）+ `_folder_dir_mtimes_match`；`sync_exist_pid_with_download_folder` 改目錄 mtime 簽章快取（命中免 walk）+ 只 shadow-write 新增差集；移除死碼 `_count_files_in_folder`。
- `app/core/thread_download.py`、`thread_pid_scan.py`：移除多餘 `_mirror_exist_pid_to_db()`（set 本就來自 DB）。
- `app/core/thread_url_fetch.py`：`_migrate_url_meta_schema` 在 `url_meta` 空時提前 return（保留 history gap-fill 契約）。

**實測前後**

| 項目 | 前 | 後（首次） | 後（快取命中） |
|------|----|-----------|---------------|
| `closed_artwork_set()` | 22.9s | 7.4s（一致 1,095,215） | ~50ms |
| `downloaded_count()` | 22.9s | — | ~85ms |
| 下載夾掃描 | ~24s | 10.5s（單趟） | 0.29ms |
| cookie_requirement 歷史解析 | ~10s | 0 | — |
| mirror 回灌 1.095M | 數秒 | 已移除 | — |

單步 init 推估 ~115s → 首次 ~18s → 暖快取 ~1-2s（6-60x）。

**驗證**：新增 17 測試（`test_closed_set_cache.py`、`test_folder_mtime_cache.py`、`test_normalize_pid_fastpath.py`）；全套件 **796 passed**；既有 `test_artwork_page_schema.py`（closed_artwork_set 正確性網）全綠 + 真實 DB count 相符；CLAUDE.md 已更新。
