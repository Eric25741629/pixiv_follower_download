# 時間戳改為「先分配」(pre-allocation) 重構

## 目標
把「下載當下才在鎖裡搶全域計數器」改成「排好隊就一次把每個 PID 的時間戳算好」。
- 每個 PID 一個時間戳,該 PID 所有頁共用。
- 不同 PID 照隊伍位置 base+0s、base+1s、base+2s...
- 下載時只查表(read-only),不需鎖、不需動全域計數器。

## 為何可行(已驗證)
- 隊伍 `order` 在下載前就完整算好(Step4 `_resolve_execution_order` / 邊查邊下 `_resolve_combined_order`)。
- 所有下載路徑都經唯一入口 `download_thread._download_pid_group`
  (Step4 single/scheduler/pool 與邊查邊下 `self.downloader` 全 funnel 到這)。
- 檔名唯一性靠 `PID{pid}{page}`,時間戳純排序用 → 共用/跳號都不會撞檔名。

## 變更點

### app/core/step4_media.py
- [ ] 新增 `assign_pid_timetags(pid_order)`:依位置建 `self._pid_timetag = {str(pid): base+i秒}`。
- [ ] `_begin_pid_timetag_block(pid=None)`:查 `self._pid_timetag[pid]` 設 thread-local base;
      查無走 fallback 懶分配(鎖內 +1s)。簽名由 `(n=1)` 改 `(pid=None)`。
- [ ] `_reserve_one_timetag()`:維持(有 block base 回傳之,否則 fallback);鎖只剩 fallback 用。
- [ ] `gif_download` 開頭 `my_time = self.download_time` 直讀 → 改 `_reserve_one_timetag()`。
- [ ] 持久化(見待決定)。

### app/core/thread_download.py
- [ ] `run()`:`_resolve_execution_order` 後、`_execute_downloads` 前呼叫 `self.assign_pid_timetags(pid_order)`。
- [ ] `_download_pid_group`:`_begin_pid_timetag_block()` → `_begin_pid_timetag_block(pid)`。
- [ ] 移除 per-PID `_emit_timechanged()`(single 1496、pool 1550)。

### app/core/thread_combined.py
- [ ] `run()`:`_resolve_combined_order` 後呼叫 `self.downloader.assign_pid_timetags([p for p,_ in order])`。
- [ ] 移除 per-PID `_emit_timechanged()`(_run_sequential 446、_run_concurrent 522),改一次性。

## 待決定:持久化時機
A) 預分配後一次 emit `base+count`(最簡、崩潰安全;半途停會跳號,跳號對排序無害)【建議】
B) high-water:`_end_pid_timetag_block` 內 `download_time=max(..,base+1s)`,結束 emit(零跳號,多幾行)

## 不動
- `tools/fix_duplicate_timetags.py` / `test_fix_duplicate_timetags.py`(修歷史資料)。
- `_apply_live_settings_if_changed`(即時路徑不再讀 self.download_time,留著無害)。

## 測試(TDD)
- [ ] 更新 `tests/test_per_pid_shared_timetag.py` 走新 API。
- [ ] 新增 `assign_pid_timetags` 建表 + 持久化測試。
- [ ] `pytest -q` 全綠。
- [ ] 真機:重啟真 app 在真資料跑邊查邊下,確認檔名/排序。

## Review
- 實作完成,採方案 A(預分配後一次存)。`pytest tests/` 1051 passed。ruff 對改動檔乾淨(僅 2 個既有 E731)。
- 獨立 review 抓到 CRITICAL Issue 1:`_apply_live_settings` 的 download_time 倒帶守衛在預分配下誤觸發
  (assign 提前 emit → `_download_time_setting_raw` 領先檔案值 → 單 PID 單 cookie 跑會把游標倒帶回 T0 卡死)。
  修正:預分配下 download_time 不再 mid-run 重套,整段移除;連帶移除死碼 `_download_time_setting_raw`、
  `_parse_live_download_time`。新增回歸測試 `test_apply_live_settings_does_not_rewind_pre_allocated_cursor`。
- 追加需求:預設時間戳起點 1970 → 2026(`settings_store.DEFAULTS` + `run_actions._parse_download_time` fallback)。
  使用者實際 live 值已是 `2026-06-01 16:15:34`(不受影響);只有全新安裝/空值會吃到 2026-01-01。
- Issue 1 修正已送原 reviewer 二次驗證(背景進行中)。
- 待辦:使用者真機重啟 app 跑一次邊查邊下,確認磁碟檔名前綴/排序與 mtime 如預期(GUI 無法代跑)。
