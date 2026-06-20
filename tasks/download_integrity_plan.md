# 下載健壯性 + 圖片完整性 (feat/download-integrity)

目標:修 mid-run 下載 hang(無法被 stop 中斷的孤兒進程)、改原子寫入、加圖片完整性驗證、提供一次性掃描 CLI。所有改動 TDD、零回歸(baseline 937 passed / 1 skipped)。

## A. Hang 修復 + 原子寫入
- [x] `app/core/pixiv_thread_base.py` 新增常數 `DOWNLOAD_BODY_TIMEOUT_SEC = 300`(與 NETWORK_RETRY_* 並列)。
- [x] `app/core/step4_media.py` `_jpg_stream_to_disk`:`iter_content` 迴圈內每塊
      (a) 檢查 stop_event → raise `DownloadInterrupted`;
      (b) 檢查 wall-clock 是否超過 deadline → raise TimeoutError。(`_check_stream_aborted`)
- [x] ugoira zip 串流(`_stream_ugoira_zip_bytes`)改為 chunk 迴圈 + stop + deadline 檢查。
- [x] 原子寫入:`_stream_to_disk_atomic` 串流寫到 `filepath + ".part"`,驗證通過後 `os.replace`;任何例外刪 `.part`。ugoira gif (`_save_ugoira_gif`) 同理。
- [x] combined 模式走同一條 `_download_pid_group` → `gif_or_jpg` 路徑,自動涵蓋(已確認,未重複)。

## B. 完整性驗證 + skip-gate
- [x] 純函式 `validate_image_file(path, fmt) -> (ok, reason)`(`app/core/image_integrity.py`)。讀檔頭+檔尾。
- [x] 下載後(rename 前)驗證;失敗 raise → 既有 jpg 5 次重試(`jpg_download`)→ 仍失敗回 `[url, ts]` 留 pending → 既有 `_shadow_mark_failures`/`err_url.txt`。
- [x] skip-gate:`_jpg_attempt` 改「存在且 `should_skip_existing` 完整才跳過」;不完整則重抓覆寫。
- [x] JXL:`_enqueue_jxl` 在 `_stream_to_disk_atomic`(已驗證)成功後才呼叫,來源天然已驗證。`.jxl` size 檢查跳過(`_run_cjxl_once` 已要求 `os.path.isfile(dst)`,非截斷問題,避免過度工程)。

## C. 一次性掃描 CLI
- [x] `app/cli/commands.py` 新增 `verify-files [--fix] [--json]`:查 `pages.get_downloaded_pages()`,
      DB `file_path` 優先、否則用 `pid_filesystem.extract_pid_pages` 反解資料夾;預設報告計數;`--fix` 截斷/遺失頁 `mark_page_pending`(並刪截斷檔)。
      `pages.file_path` 實測未由 live 下載路徑寫入(`mark_urls_done` 不含),故以資料夾反解為主、`file_path` 為輔。對齊 CLI 契約。

## 測試(先 failing test,全綠)
- [x] `tests/test_image_integrity.py`(15)、`tests/test_download_stream_integrity.py`(6,stop/deadline/atomic)、`tests/test_download_skip_gate.py`(5)、`tests/test_cli_verify_files.py`(4)。
- [x] 全套 `python -m pytest -q` → 967 passed, 1 skipped(baseline 937 + 30 新測試),零失敗。

## 約束
- 使用者可見字串繁中。寫檔走 atomic/os.replace。commit 分邏輯 commit、慣例式訊息、**不加 Claude co-author trailer**。
