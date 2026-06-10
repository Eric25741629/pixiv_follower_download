# Lessons

## 邊查邊下:整體進度條在「進入下一個 PID」時消失(真根因,推翻下面那條 Row reflow 假設)

症狀(使用者多次回報,視窗全程開著):整體進度條+總數+ETA 其實**會顯示**,但只在「某 PID 全部分頁下載完、下一個 PID 還沒進 p0」的空檔出現;**一進入下一個 PID 的 p0 就整個消失**。本作分頁與 log 全程正常。

前三輪都誤判成「Row 沒更新/page.update flush 不到」(見下一條),補了 `row.update()` 仍沒好。讀 Flet 0.84 安裝原始碼後確認:無參數 `page.update()` 會用每個控制的 `_dirty`/`__changes` 從根遞迴帶出子控制變更(`object_patch.py:515-517,1012-1080`),dispatcher 每 50ms 就呼叫一次,所以「子控制改值不會 flush」的理論在原始碼層級站不住腳。

真根因:combined(`thread_combined.py`)**組合**了 fetcher(`get_img_url_thread`)+ downloader 共用同一條 event queue,且從不呼叫它們的 run()。fetcher 的 `get_download_url()` → `_step3_finish_pid()` 每個 PID 都呼叫 `_step3_advance_progress()`(`thread_url_fetch.py:2014`),送 `WorkerEvent("progress", (1, self.pid_max))`。而 `self.pid_max` 只在 `_load_and_filter_pid_list()`(run() 路徑)被設定,combined 從不走那裡 → **fetcher.pid_max 永遠是 class 預設 0**(`thread_url_fetch.py:49`)。於是每查一個 PID 就送 `progress(1, 0)` → `MainView.update_progress(1, 0)` → `total<=0` → 把整體進度清空/隱藏。combined 自己的 run() 迴圈在每個 PID 完成後送 `progress(1, len(order))`(total 正確),所以才會「完成時閃一下、下一個 PID 又消失」。downloader 的逐頁 progress 早就被 `_CombinedPageProgressQueue` 攔截轉成 page_progress(所以本作分頁正常),但 **fetcher 的 progress 沒人攔**。

修法:在 `_process_one_pid` 查詢期間把 `self.fetcher._q` 換成 `_DropOverallProgressQueue`(丟棄 type=="progress"、其餘照過),combined.run() 成為整體進度的唯一發送者;順手把兩條進度列統一成同一個 builder + 同一套 render(visible-gated)。測試 `tests/test_combined_overall_progress_kept.py`、`tests/test_main_view_progress_render.py`。

規則:
1. **UI 數值「消失/被清空」→ 列出共用 queue 上的「所有」發送者**,特別是被「組合/重用」的子執行緒。一個被 compose 進來、但其計數器(pid_max)在新情境沒被初始化(=0)的執行緒,會送出帶 0 的事件污染共用 UI 狀態。
2. 重用一個原本「整段流程(run())」設計的執行緒、卻只呼叫它的局部方法時,要檢查**哪些副作用(進度發送、計數器、檔案 flush)原本依賴 run() 的前置設定**,在新情境會變成未初始化/錯誤值。
3. 看「會動 vs 不會動」的兄弟控制差異固然對,但更要看「**是誰在改它**」——這次是「有別的發送者用 total=0 把它打回原形」,不是「沒更新」。
4. 連猜三次都沒中,就别再加修正了;去讀框架原始碼證偽/證實「flush 機制」假設,並用實機 log(`%APPDATA%/pixiv_download/app.log`)+ 精確症狀(哪個瞬間消失)定位事件來源。

## Flet:文字在 expand ProgressBar 旁邊不即時更新,卻在「無關互動後」整批出現

> 修正(2026-06-09):下面這條當時被當成「整體進度失效」的根因,但其實**沒解決使用者的 combined 模式問題**(真根因見上一條)。`row.update()` 對「子控制改值卻不重排」也許在某些版面有幫助,但 dispatcher 本來就每 50ms 整頁 `page.update()`,所以它不是 combined 消失的主因。保留此條作為「被推翻的假設」紀錄。

症狀(使用者回報):邊查邊下時整體進度 `K/N`、預計剩餘時間都不顯示,**按下「結束」後才一次出現**。

錯誤的第一直覺:以為是「progress 事件每個 PID 才發一次、第一個 PID 慢 → 看起來壞掉」(granularity)。使用者直接糾正:「PID/total、ETA 都消失了,不是你說的那樣」。

真根因:`update_progress` / `update_countdown` 只呼叫子控制 `self._progress_text.update()`,**從不更新外層 Row**(`progress_row` 只是 `build()` 的區域變數)。含 `expand=True` ProgressBar 的 Row,子控制的 `.value` patch 不會觸發 Row 重新排版文字 → 文字停在空白,直到按 stop 觸發 loading dialog 的整頁 `page.update()` 才整頁 relayout,才「自己出現」。對照 `_page_progress_row` 一直正常,因為它有呼叫 `row.update()`。

規則:
1. Flet 中「子控制改了 .value 但畫面沒動,直到拖視窗/點擊/開 dialog 才更新」→ 先檢查是不是**只更新了子控制、沒更新外層容器(Row/Column)**。修法是 `parent_row.update()`,不是只 `child.update()`。對照同畫面「會動」的兄弟控制,看它多做了什麼。
2. 不要先假設是 granularity / 後端事件頻率。使用者說「按 X 後才出現」幾乎都是**渲染/ reflow 時機**問題,不是資料問題。
3. 被使用者糾正後,回去找「會動 vs 不會動」的差異,而不是辯護原假設。
4. 改 Flet UI 後若使用者說「一樣沒效」,先確認是否**沒重啟 app**(Flet 桌面端不會熱載入 Python),再懷疑修正不足。

## 邊查邊下:中止 mid-PID 把未下載頁面誤判成功 → 下次整批跳過

8 張的 PID,下載到第 5 張按中止,下次不續傳。根因:`_download_pid_group` 的迴圈只把**已嘗試且失敗**的 URL 放進 `failed`;因 stop 而 break 後,還沒下載的頁面**不在 `failed` 也不在任何回傳**裡 → `failed=[]`。combined `_process_one_pid` 把空 list 當成「全部成功」→ `_mark_urls_done(全部)` + `_maybe_flush_exist_pid`(關閉 PID)+ `_mark_pid_processed`(移出 pictures_id.txt)。下次該 PID 進 closed set 被整批跳過。

規則:
1. 「回傳空失敗清單」不等於「全部完成」。被 stop / break 中斷的迴圈,**未處理項目要明確標記為未完成**,不能讓「沒失敗」被解讀成「成功」。
2. 批次標記完成前,先檢查 `stop_event`:中止時走「不標記完成」分支,讓已 seed 的 pending 列與 pending PID 檔保留,下次自動續傳(基礎設施已存在,只是被誤標清掉)。
3. 寧可中止後下次重跑該 PID(已存在的檔會跳過/覆寫),也不要漏圖 — 正確性 > 一點點重工。
