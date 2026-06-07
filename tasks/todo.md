---
goal: download.author_order 開啟時，combined mode 下載順序 + 步驟2 pictures_id.txt 實體順序都「同作者連續」
started: 2026-06-07
status: in_progress
spec: docs/superpowers/specs/2026-06-07-combined-author-order-design.md
---

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
