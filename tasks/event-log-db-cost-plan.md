---
goal: 加速崩潰恢復、避免啟動白屏、降低事件記錄/DB 寫入成本（確保資料安全）
started: 2026-05-31
status: research-complete / awaiting-decision
method: 20-agent workflow (code readers + web research + adversarial verification)
status_note: IMPLEMENTED 2026-05-31 — Tier 1-3 landed; QW2 rejected (unsafe); STR5 reduced to backup_db TRUNCATE; 686 tests green.
---

## 三個根因（已用程式碼證據確認）

1. **每筆事件都 `os.fsync`** — `event_log.py:160-163` 每次 `emit()` 都 write()+flush()+os.fsync()。
   每個 DB mutation 在寫 DB *之前* 先 emit 一筆（`metadata_db.py:951` 等），所以每張圖的
   `mark_page_downloaded` 都強制一次實體磁碟 flush（Windows FlushFileBuffers ~1-10ms），且序列化在
   單一 `EventLog._lock` 後面。**這就是「寫資料庫代價很高」的真正來源** —— SQLite 本身在
   WAL+synchronous=NORMAL 下並不會每次 commit fsync（`metadata_db.py:165-166`），DB 寫入其實很便宜。

2. **Step 3 的 O(N²) 累積重寫** — `thread_url_fetch.py:_write_all_url_snapshot` 每 25 個 PID flush
   一次，但每次都把「到目前為止的整個 URL 集合」整包丟給 `db.upsert_pending_urls` →
   `upsert_pages_bulk` → 一筆超肥的 `pages.upsert_bulk` 事件（內嵌完整 i.pximg.net URL，
   `metadata_db.py:1045`）。第 k 批重寫 ~25*k 筆 → 位元組量呈二次成長。這是 2-3 GB/天 與
   11 GB 積壓的主因，也讓每批 SQLite 寫入成本隨執行時間爬升（`import_meta_dict` 同樣每批重寫整個 dict）。

3. **recover_tail 是 O(全部位元組) 兩遍掃描** — `event_log.py` 先掃一遍找 cutoff（不提早 break），
   再掃一遍套用。11 GB 上做數千萬次 json.loads → 啟動白屏 → 使用者強制關閉 → session 保持 unclean →
   下次啟動再來一次。（目前已加 256 MB 上限當 stopgap。）

次要：`_detect_unclean` 用 `readlines()` 整檔讀進記憶體（巨檔 OOM 風險）；保留期只有 60 天 mtime、
無位元組上限；恢復 anchor（snapshot/session.shutdown）在「強制關閉且當天沒按 Run」時根本不存在。

## 關鍵戰略發現

SQLite WAL 重開時會自動重播自己的 -wal 恢復已 commit 的交易 —— **JSONL 事件記錄對「活的 DB」
的崩潰耐久性貢獻為零**。它唯一獨有的價值是：時間點重建 / 稽核 / 整個 .sqlite3 檔毀損時的跨檔恢復。
對這個「資料可重爬、DB 才是真相」的個人爬蟲，多 GB 的歷史 JSONL 主要是負債。
→ 正確方向：事件記錄縮成「上次快照以來的小尾巴」，由定期 DB 快照當恢復地基。

## 對抗式驗證後的優先順序（每項都附 verdict）

### Tier 1 — 直接解決使用者痛點，全部 SAFE、低成本（建議先做）

- **A [QW3] 批次化事件 fsync**（S，data_safe=✔ modify）
  每 N 筆 / 每 ~1s fsync 一次，而非每行；`close()` 與 `session.shutdown`/`snapshot` anchor 仍強制
  fsync；每行仍保留 `flush()`。**用 EventLog 建構子參數傳入設定，不要在 event_log.py 內 import
  settings**（層級邊界）。→ 移除每筆寫入的磁碟 barrier。
- **B [QW1] 終結 Step 3 的 O(N²) 重寫**（M，data_safe=✔ modify）
  只把 delta（`merged − 本次已 flush 集合`，**不是** new_urls）丟給 upsert_pending_urls；最後一次
  flush 補送完整集合當保險；all_url.txt 仍寫完整。→ 2-3 GB/天 降為線性。
- **C [STR1] recover_tail 改 O(tail)**（M，data_safe=✔ modify）
  反向 seek（**區塊串流，不可用 readlines**）找最後 anchor，只套用尾巴；保留字串比較；256 MB 上限
  先留著，等這項落地測過後在另一個 commit 移除。→ 永久解決白屏。

### Tier 2 — 加固 / 安全網，SAFE

- **D [QW6] _detect_unclean 改尾讀**（S，✔ modify）反向區塊讀 + 掃描位元組上限（超過就保守判為
  unclean），跨區塊半行要接續。可重用 C 的 helper。
- **E [QW5] 位元組上限保留**（S，✔ modify）刪到總量低於上限為止；**anchor 所在檔本身也要保護
  （inclusive）**，否則 replay() 找不到 snapshot_ts 會靜默重建不完整。建構子參數傳入。
- **F [STR2] 啟動時保證一個 anchor**（M，data_safe=✘→✔ 需改 ordering）
  emit 一個便宜的 `checkpoint` anchor，**必須在 recover_tail 之後**（在之前會把 cutoff 推到未來、
  反而吞掉要恢復的孤兒事件）；加進 cutoff set 與 `_META_KINDS`。

### Tier 3 — 結構性，較大 / 之後

- **G [STR3] 快照錨定恢復 + 快照後壓實**（L，✔ modify）由結構面 bound 住記錄量；**刪檔前先 fsync+
  integrity_check 快照**；用事件時間戳而非檔名判斷；建議排在 A/B 之後。
- **H [STR4] 依大小輪替**（M，data_safe=✘→✔）每天第一個檔也要用 `.000`（裸日期 vs `.NNN` 的排序
  bug 會讓 _detect_unclean 誤判 clean → 崩潰後靜默資料遺失）。
- **I [QW4 正確版]** 新增 `pages.downloaded_bulk` 事件種類 + dispatch handler；順手修一個既有潛在 bug：
  目前 `pages.upsert_bulk` replay 用 INSERT OR IGNORE，恢復後已下載的頁會卡在 'pending' 被重抓。

### 駁回 / 瑣碎

- **QW2（丟 URL、replay 重建）= 不安全，駁回原樣**：實測 ~9% 頁會 404（`pages.url` 存的是去掉 hash 的
  canonical 形式，但 `img_url_template` 保留 hash → 重建出錯路徑）；且改 3-tuple 會讓 upsert_pages_bulk
  整批 ValueError 丟光。若 A/B 之後還要再瘦，改用**無損前綴壓縮**（共同 URL 前綴抽一次）。
- **STR5 多數駁回**：`busy_timeout=5000` 會把現有 30s（connect timeout 已等於 busy_timeout）**調低**、
  更容易 lock；熱路徑其實已經是 bulk。只保留 backup_db 的 PASSIVE→TRUNCATE。
- **STR6 縮成只留 UI 提示**（「caps future launches」說法錯：上限是加總所有檔，append 一行不會變小）。
- **QW7 瑣碎文件修正**：只改 CLAUDE.md「Legacy tables」那一句（四個 legacy 表已在 Phase 8 DROP，零寫入）；
  **不要動 PHASE-A 的 exist_pid shadow-write 段落（那是真的、還在用）**。

## 先量測再動手（workflow 建議）

1. 列 events/ 各檔大小、看最肥那行確認是 `pages.upsert_bulk` + 完整 URL（已間接確認）。
2. 同一個 Step 3 真跑，記錄 A/B 前後 events-*.jsonl 每日位元組，證明二次→線性。
3. 動 256 MB 上限前，先量一個代表性檔的 recover_tail 牆鐘時間。
4. `event_log=None` vs 啟用，量 N 次 upsert_pending_urls，歸因 fsync vs autocommit vs O(N²)。

## 安全總則

所有 DB mutator 皆 idempotent（INSERT OR IGNORE / ON CONFLICT DO UPDATE），所以恢復「多套用」永遠安全，
真正風險是「少套用」——靠 anchor 往回走到底來防。批次化 fsync 唯一犧牲：硬斷電時遺失最後 <interval 的
*事件記錄*行（DB 的 WAL 仍保有那些 row，且資料可重爬）。保留期/壓實刪檔絕不可跨過最新 snapshot anchor 檔。
