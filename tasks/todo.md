---
goal: download.author_order 開啟時，combined mode 下載順序 + 步驟2 pictures_id.txt 實體順序都「同作者連續」
started: 2026-06-07
status: in_progress
spec: docs/superpowers/specs/2026-06-07-combined-author-order-design.md
---

## [2026-06-14] cookie「檢查：時間」整輪凍結 — 真根因是「每輪只刷一次」(承上題)

**使用者再回報**:cookies 頁時間還是停在 21:13~21:51,整輪不動(「沒變化/還是沒有刷新」)。**用實機資料證偽了我前一個假設**:現在 00:21,`settings.json` mtime 00:20(這輪一直在寫 `download_time`),但 10 個 cookie 的 `last_tested_at` 全凍在 21:xx(各自「首次使用時間」)。所以不是 settings 競態被吃掉,是 worker 根本沒再刷。

**真根因**:有效時間刷新掛在 `AccountScheduler` 的 `on_first_success`,`_mark_success_locked` 用 `_used_cookies` 閘門讓它**每個 cookie 每輪只觸發一次**(首次成功)。長時間跑一輪時,每個 cookie 早早各刷一次就凍住,使用者看到的就是「停在某時間點不動」。前一題修的 settings RMW 競態是真 bug 但只是次要;真正卡的是這個「每輪一次」設計。

**修復**:`on_first_success` → 改名 `on_success`,`_mark_success_locked` **每次** `release(ok=True)` 都回呼(移除 `_used_cookies` 閘門)→ `_refresh_cookie_timestamp` 每次成功使用都更新 `last_tested_at=now` 並 emit `cookie_status` 事件 → cookies 頁「檢查：」時間隨每次使用(約每 cooldown 秒一次)即時前進。
- `app/core/account_scheduler.py`:param/attr 改名、移除 `_used_cookies`、`_mark_success_locked` 每次回傳 callback。
- `app/gui/run_actions.py:542`:`on_success=` 接線。
- 3 個測試 kwarg 改名(皆單次使用、斷言不變);新增 `test_on_success_fires_on_every_release_not_just_first`(同 cookie release 3 次 → 回呼 3 次,舊碼只 1 次)。
- 全測 **916 passed**。
- **與前一題互補**:每次成功都刷 = auth 寫入變頻繁,正好需要前一題的 settings per-path 鎖才不會跟每 PID 的 `download_time` 寫互相吃掉。三個修法(鎖 + 每次刷 + on_disable 即時 emit)合起來才是完整的「動態 cookie 狀態」。

**⚠️ 必做**:Flet 不熱載入。使用者若從 21:13 那輪一直沒重啟,**我所有改動都還沒載入**(他一直在看舊碼)。**需重啟 app** 後再跑 combined 驗證「檢查：」會前進。

## [2026-06-13] 拆分 >1000 行檔案 + 重構(程式碼復用/簡化/性能)— 進行中

**目標(使用者)**:把超過 1000 行的檔案拆分、重構、提升復用、簡化邏輯、優化性能。**範圍決定:完整計畫(含高風險)。**

**4 個超標檔(唯讀分析 agent 已掃)**:
| 檔案 | 行數 | 整體風險 |
|------|------|----------|
| `app/core/thread_download.py` | 3080 | medium |
| `app/core/thread_url_fetch.py` | 2092 | high |
| `app/core/metadata_db.py` | 1448 | low |
| `app/core/pixiv_thread_utils.py` | 1011 | low |

**手法**:一次一個 transformation、每步跑全測綠(`pytest -m "not integration"`,基線 913→現 915);純函式用「Move Function + re-export shim」讓 `from ...pixiv_thread_utils import X` caller 零改動;Thread/DB 類別方法群要抽 helper class + 依賴注入(較高風險,逐一做、每塊停下讓使用者跑真實 combined 驗無回歸)。**不平行盲拆。**

### 進度

- [x] **`pid_utils.py`**(新,104 行):`normalize_pid`/`normalize_pid_set`/`canonicalize_pximg_url_for_storage`/`_extract_pid_candidates_from_name`/`_PID_FROM_NAME_PATTERNS` 從 `pixiv_thread_utils` 搬出,原檔 re-export。純 stdlib、零耦合。全測 915 綠。
- [x] **`cookie_utils.py`**(新,215 行):12 個 cookie pool 解析/去重/別名/usage-label 函式搬出,re-export。純 stdlib、零耦合。全測 915 綠。
- [x] `pixiv_thread_utils.py`:**1011 → 750 行(已低於 1000 ✓)**。← 4 檔解決 1 個。
- [x] **`metadata_db.py`(1448 → 981,<1000 ✓,2026-06-14)**:4 個 transformation,每步全測 916 綠 + ruff 綠。抽出 `metadata_db_schema.py`(92,DDL 純搬移)、`metadata_db_cache.py`(48,closed-set 快取原語 + re-export)、`metadata_db_migration.py`(154,`_MigrationMixin`)、`metadata_db_artwork.py`(265,`_ArtworkMixin`)。手法:純常數/函式用 Move+re-export(import 放頂端避 E402);公開 API 方法群用 mixin(`MetadataDB(_MigrationMixin,_ArtworkMixin)`,`self._conn/_lock/_emit/_bulk_write/_coerce_pid` 留具體類別)。pages CRUD(~270)未抽,981 已達標。
- [~] **`thread_download.py`(3080 → 2598,2026-06-14)**:兩個低風險塊已抽(mixin 手法,全測 916 綠 + ruff 綠)。`step4_filename.py`(210,`_FilenameMixin`,7 方法+3 regex;regex byte 比對驗證)、`step4_jxl_conversion.py`(337,`_JXLMixin`,21 方法+常數,script 依行號機械抽取;清掉 4 個未用 import)。**剩高風險塊**(`step4_url_filter`/`step4_ugoira`/`step4_download_execute`,碰 scheduler/stop/url_meta/exist_pid 共享狀態)**待使用者實機驗證後再做**。
- [ ] **`thread_url_fetch.py`(2092,整體 high)**:`step3_filters.py` 先行(~270),再 `step3_io.py`/`step3_cache.py`/`step3_query.py`/`step3_cookie_requirement.py`(全 high,需 DI、最後做)。
- [ ] 跨檔復用(分析 agent 另列):`pixiv_thread_base` 集中 `_init_cookie_fields`/事件 emit helper/`_init_pid_cookie_selection`;`metadata_db` 包一個 init+stats+mirror factory。先把上面單檔拆穩再做。

**注意**:剩餘三檔的拆分多為 Thread/DB 類別共享 `self` 狀態的方法群,非純函式搬移(分析 `coupling_notes` 標 medium/high),每塊抽完都要實機驗證(使用者偏好:GUI/threading 綠測不算數)。

## [2026-06-13] combined 模式 cookie 狀態在 GUI 不刷新 — settings.json RMW 競態(根治)

**現象(使用者回報)**:邊查邊下(步驟3+4)時下載明明成功、cookie 可用,但 cookies 頁狀態/「檢查時間」不會動態更新,「為什麼不是動態的」。只有單跑步驟 3 看起來正常。

**根因(競態,非邏輯錯)**:`SettingsStore.update_section/update_fields` 是 `load()→改一個 section→save() 整份檔` 的 read-modify-write,**完全沒有鎖**;`_store()`/`_settings_store()` 每次都 new 一個實例。combined 模式下兩條執行緒同時寫 `settings.json`:
- dispatcher 執行緒,**每個 PID**:`handle_timechanged → update_fields("download", {download_time})`(combined 在 `thread_combined.py:471` 每 PID emit `timechanged`)。
- worker 執行緒,每個 cookie 首次成功:`on_first_success → _refresh_cookie_timestamp → update_section("auth", ...)`。

兩者各自 `load→改自己 section→save 整份`,誰後存誰就用自己的舊快照覆蓋掉對方那個 section。cookie 的 `last_tested_at` 刷新(auth)被 download 寫入回退 → GUI 顯示舊狀態。是競態所以「有時會有時不會」(實測 10 cookie 有 6 個刷新成功、4 個被吃掉)。步驟 3 單獨跑不會 emit `timechanged`,沒有競爭者,所以看起來正常 — 完全對上使用者「3+4 整合問題」的直覺。

**修復**:
- [x] `app/core/settings_store.py`:新增 module 級 per-path `threading.RLock`(`_lock_for_path`,key=正規化絕對路徑,所有實例共用),把 `update_section`/`update_fields`/`update_multiple`/`migrate_from_legacy` 的 load+save 包進鎖內。RLock 可重入、只在快速檔案 I/O 期間持有,不影響網路/長任務。
- [x] `app/gui/run_actions.py`:`_invalidate_cookie_status`(on_disable)補上 `WorkerEvent("cookie_status", (cookie, "失效", now))`,與 `_refresh_cookie_timestamp` 對稱 — cookie 跑到一半被禁用時即時翻成失效,不必等下次 reload。
- [x] 測試先紅後綠:`tests/test_settings_store.py::test_concurrent_section_writes_do_not_clobber`(決定性交錯,A 在 RMW 中途 park、B 寫另一 section)+ `test_parallel_writers_lose_no_updates`(壓力:未修前 60 次只剩 27 次,丟 33 次);`tests/test_run_actions_scheduler.py::test_invalidate_cookie_status_writes_失效` 擴充斷言有 emit cookie_status。
- [x] 全測試 915 passed(原 913 + 2 併發)。

**待辦(使用者偏好:GUI/threading 改動須實機驗證,綠測不算數)**:
- [ ] 實機驗證:重啟 Flet app,跑 combined 一輪,停在 cookies 頁觀察「檢查：時間」會隨每個 cookie 首次成功即時更新;故意讓某 cookie 失效確認即時翻紅。(我無法在 headless 驅動 GUI,需使用者執行)

**殘留(已知、非本次回報情境)**:pool 模式多 worker 併發呼叫 `_refresh_cookie_timestamp` 時,各自用自己的 `get_section("auth")` 快照重建整個 `cookies_entries` 再覆蓋,理論上仍可能互相吃掉(combined 是單 worker 順序處理,不受影響)。若日後 pool 也要即時刷新,應改走「鎖內 re-read + 只改該 cookie」的 atomic mutate。

## [2026-06-13] download_time 不持久化(檔名大量同前綴)+ review 7 findings + 狀態顯示 + cookies 按鈕 + 時間戳設定

**緊急根因(使用者回報大量 `20260330_084355` 開頭檔名)**:`thread_download.py:2484` 在 Step4 結束才 emit `WorkerEvent("timechanged", ...)`,但 `flet_app.py` 的 dispatcher handlers **沒有註冊 "timechanged"** → `download.download_time` 永不回寫設定 → 每次執行從同一起點編時間戳。combined 模式連 emit 都沒有。另外 `_apply_live_settings_if_changed`(thread_download.py:417)每次設定變更都把進行中的 `download_time` 重設回設定檔值。

- [x] A1 flet_app 註冊 "timechanged" handler → `update_fields("download", {"download_time": ...})`
- [x] A2 downloader 每 PID 完成 emit timechanged(單執行緒+pool);combined run loop 每 PID emit
- [x] A3 `_apply_live_settings_if_changed` 以 `_download_time_setting_raw` 追蹤,只在使用者真的改欄位時套用(GUI 回寫不會倒帶計數器)
- [x] B 設定頁新增「下載時間戳起點 (YYYY-MM-DD HH:MM:SS)」TextField(download.download_time,進 save() 明列)
- [x] C `_apply_download_mtime`:jpg/gif 存檔後 os.utime 設為 timetag;`download.set_file_mtime` 預設 True(DEFAULTS + 開關 autosave + live refresh + step4/combined 接線)
- [x] D1 AccountState.held;acquire 標記/跳過 held;release/release_neutral/disable 清除;全 held 時 poll 0.5s
- [x] D2 pool `pool_stopped`:stop tuple 不記為成功、不送後續 batch;「所有 Cookie 都已禁用」只 emit 一次
- [x] D3+D5 `_download_pid_with_scheduler`:stop 中斷或非網路例外 → `release_neutral`(不禁用、不刷新 last_tested_at、仍有冷卻);僅重試耗盡 → release(ok=False)
- [x] D4+D7 刪 lazy threading.local 與 legacy `_current_account` 雙軌;combined 改呼叫 `_set/_clear_current_download_account`
- [x] D6 countdown 非零 tick 節流 1/s(零值即時通過)
- [x] E phase:step3 run loop「正在查詢: PID」、step4 單/pool「正在下載: PID」、combined 查詢/下載兩段各自 emit
- [x] F cookies 頁 6 顆 glass_pill 統一 width=104
- [x] 測試:新增 tests/test_scheduler_multiconsumer_and_timechanged.py(14);全套件 909 passed, 1 deselected;CLAUDE.md 已更新(scheduler 段 + timetag 持久化新段)
- [ ] 實機驗證:重啟 app 跑一輪,確認設定頁時間戳欄位、檔名前綴遞增且關閉後重開不重複、檔案 mtime、phase 顯示、cookies 按鈕同寬

**Review(2026-06-13)**:根因=「timechanged」事件從 PyQt 遷移後無人接(舊 Qt UI 欄位即時更新+存檔,Flet 版漏掉 handler),且 combined 模式從不 emit、live-settings 每次儲存都重設計數器 — 三層疊加造成大量同前綴檔名。修復走事件既有路徑(worker emit → dispatcher → settings 寫回),不讓 worker 直接寫設定檔,維持單寫者。release 語意三分(ok / 失敗 / 中立)是 stop 誤殺與失敗刷新兩個 finding 的共同根治。

## [2026-06-12] 步驟4 多執行緒(pool)模式完全繞過 AccountScheduler — cookie 有效時間不刷新(待修)

**現象(使用者回報)**:下載成功也不會刷新 cookie 的有效時間(`last_tested_at` / 30 天信任快取),目前只有跑步驟 3 才會刷新。

**機制**:有效時間刷新掛在 `AccountScheduler` 的 `on_first_success` 回呼(`app/gui/run_actions.py:536` → `_refresh_cookie_timestamp`),每個 cookie 在該次執行第一次 `release(ok=True)` 時把 `last_tested_at` 更新成現在。只更新時間戳、不改 status,所以後續 `_invalidate_cookie_status` 仍可覆寫成失效。

**根因**:回呼步驟 2/3/4 都有接(三個 build 都走 `_build_scheduler`),但實際觸發點是 worker 有沒有走 `_acquire_account()` / `_release_account()`:

- 步驟 3(`thread_url_fetch.py:1468-1479`)每個 PID 都 acquire/release → 會刷新。
- 步驟 2(`thread_pid_scan.py:179/452`)也有 → 會刷新。
- combined(`thread_combined.py:294/365`)也有 → 會刷新。
- **步驟 4 只有單執行緒模式**(`_execute_downloads_single` → `_download_pid_with_scheduler`,`thread_download.py:2109-2122`)有 acquire/release。**多執行緒 pool 模式**(`_execute_downloads_pool`,`thread_download.py:2185-2192`)直接 `executor.submit(self._download_pid_group, ...)`,完全不經 scheduler → `on_first_success` 永遠不會觸發,有效時間不刷新。

**連帶影響(比時間戳更嚴重)**:pool 模式下 per-account cooldown、throughput gate、proxy 失效禁用(`on_disable` → `_invalidate_cookie_status`)全都沒生效,不只是刷新問題。

**修法方向**:讓 `_execute_downloads_pool` 的每個 PID 工作也包進 `_download_pid_with_scheduler`(或至少每個 PID 成功後 `scheduler.release(acc, ok=True)`);注意 pool 是 4 worker 併發,scheduler 的 acquire 阻塞語意要確認與 ThreadPoolExecutor 相容(acquire 在 worker thread 內呼叫、單一 batch barrier 不被破壞)。

- [x] 先寫測試:pool 模式下載成功 → scheduler.release(ok=True) 被呼叫、on_first_success 觸發(紅)
- [x] `_execute_downloads_pool` 接上 acquire/release(沿用 `_download_pid_with_scheduler` 或等價包裝)
- [x] 確認 cooldown/`on_disable` 在 pool 模式同樣生效;`_pid_cookie_selection` sticky 邏輯不回歸
- [x] 全測試: `python -m pytest -m "not integration" -q` → 895 passed, 1 deselected
- [ ] ruff:目前 `python -m ruff` / `.conda\python.exe -m ruff` / `ruff` 都不可用(未安裝)
- [ ] 實機驗證:pool 模式下載成功後 cookies 頁 `last_tested_at` 即時更新

**Review(2026-06-12)**:
- 新增 pool scheduler regression tests:成功下載觸發 `on_first_success`;network/proxy 失敗觸發 `on_disable`;併發 worker 使用 thread-local account,避免共用 `_current_account` 互相覆蓋。
- `app/core/thread_download.py`:`_execute_downloads_pool` 在有 scheduler 時改送 `_download_pid_with_scheduler`,保留每個作者 batch drain 完再進下一批的 barrier;無 scheduler 時維持直接 `_download_pid_group`。
- `_download_pid_group` 改讀 `_current_download_account()`;scheduler worker 使用 thread-local account,combined mode 仍可透過既有 `_current_account` fallback。
- 驗證:`tests/test_execute_downloads_pool_author_barrier.py` 紅測先失敗(3 failed, 3 passed)→修正後 6 passed;相關測試 42 passed;全非 integration 測試 895 passed,1 deselected。

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

## 2026-06-13 冷卻語意改固定單帳號秒數 + 查詢階段倒數 + 設定頁滑桿修復

- [x] account_scheduler：拿掉 `× ln(N+1)` — 單帳號冷卻 = 設定值（固定 N 秒），throughput = avg / N
- [x] thread_combined：「正在查詢：PID」phase 移到 acquire 之前，倒數顯示時標籤正確
- [x] settings_view：滑桿 min 0 / inactive 軌道顏色可見 / 提示文字改新公式 / 欄位語意改單帳號
- [x] 測試同步更新（test_account_scheduler.py）+ 全綠
- [x] CLAUDE.md 冷卻段落更新；pid_cooldown_avg 設 45
