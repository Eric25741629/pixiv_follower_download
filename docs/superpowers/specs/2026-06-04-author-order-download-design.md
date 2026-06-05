---
goal: Step 4 下載時「依作者順序」— 同一作者的作品連續下載完再換下一位
date: 2026-06-04
status: design-approved
---

## 動機

使用者要求:下載時把同一個作者的所有作品「連續」下載完,再換下一位作者。

現況(已查證):Step 4 的下載順序沒有任何明確排序,是 SQLite rowid(插入)順序經一串 Python list 操作後的湧現結果。

- `get_pending_urls_filtered()` 無 `ORDER BY`(`app/core/metadata_db.py:698-716`,`like_min>0` 與 `like_min=0` 兩條分支都沒有)。
- DB 列順序被 list comprehension 原封帶入 `self.allurl`(`app/core/thread_download.py:401`);DB 不可用時 fallback 讀 `all_url.txt` 行順序(`:414`);先前失敗的 URL 在讀取階段 `extend()` 併到清單尾端(`_enqueue_retriable_failures`,`:442`)。
- `_group_urls_by_pid()` 以「首次出現順序」決定 PID 序列(`order.append(pid)`,`:1786-1797`)——**這是定義下載順序的唯一咽喉點**。
- 單執行緒 `_execute_downloads_single` 循序消費 `pid_order`(`:1979`);多執行緒 `_execute_downloads_pool` 一次把所有 PID 都 submit 進 pool 再用 `as_completed`(`:2027-2033`)。

因為 Step 2 一次掃一位作者,今天的順序「碰巧」大致依作者群聚,但跨多次執行、失敗重排、fallback 路徑都會破壞,**沒有保證**。

作者資料已可用:`artworks` 表已有 `user_id` / `user_name` 兩個 `TEXT` 欄位(`metadata_db.py:56-57`,idempotent `ALTER TABLE` 自動遷移 `:193-197`),由進行中的 Step 2/3 接線填入(Step 2 `(pid, user_id)` 經 `upsert_pending_pids`,`metadata_db.py:729-760`;Step 3 `Pixiv_info` 回傳 8 元素含 user_id,`pixiv_api.py:754-765`)。但**沒有任何 view 對外露出 user_id**,且舊資料的 `user_id` 常為 NULL。

## 使用者已拍板的四個行為決定

1. **作者之間**:維持發現順序 — 以每位作者在 `pid_order` 中第一次出現的位置決定先後。
2. **作者內部**:PID 由大到小(新圖先);數字排序,非數字 fallback 字典序。
3. **作者不明**(`user_id` 為 NULL / 空字串):整批排到最後面;此桶內部同樣套 PID 由大到小。
4. **並行嚴格度**:嚴格一作者一作者。單執行緒本來就循序、天然滿足;多執行緒(pool)需在每位作者之間加 barrier。

## 方法選擇

採 **方案 A:記憶體內重排**。在 `_group_urls_by_pid` 算出 `pid_order` 後、執行下載前做一次穩定重排。

- 排的是最終 `pid_order`,涵蓋所有來源(DB / `all_url.txt` fallback / 失敗重排,因為三者在分組前都已併入 `self.allurl`)。
- 零 schema 變更、零 SQL 變更、不動 views。
- 開關關閉時完全不碰原行為(零回歸)。

否決:
- 方案 B(SQL `ORDER BY`):`like_min=0` 分支目前無 join,要動熱路徑;且 SQL 排序之後又被 `_group_urls_by_pid` 的首次出現邏輯覆蓋,fallback 路徑也繞過 → 不可靠。
- 方案 C(新 view / 欄位):最重,只有在要把作者順序變全體預設時才值得。

## 架構與觸及點

### 新增 1 — DB 批次查詢

`app/core/metadata_db.py`,置於 `get_artwork`(`:907`)旁:

```
user_id_map_for_pids(pids: Iterable[str]) -> dict[str, str | None]
```

- 內部對每個 pid 用 `_coerce_pid` 對齊 `artworks.pid`,分塊查 `SELECT pid, user_id FROM artworks WHERE pid IN (...)`(每塊 ≤ 900 個,避開 SQLite 變數上限)。
- 回傳以**呼叫端傳入的原始 pid 字串**為鍵(避免正規化不一致導致對不上)。pid 不在 `artworks`、或 `user_id` 為 NULL/空 → 值為 `None`。

### 新增 2 — 純函式重排(無 DB 依賴,可單測)

`app/core/thread_download.py`,module-level 純函式:

```
compute_author_order(pid_order: list[str], pid_to_user_id: dict[str, str | None])
    -> tuple[list[str], list[list[str]]]
```

回傳 `(flat_order, author_batches)`:
- `flat_order`:重排後的扁平 PID 序列(給單執行緒與進度計數)。
- `author_batches`:`list[list[pid]]`,一位作者一個 batch,依作者首次出現順序排;作者不明的那個 batch 永遠放最後。

演算法:
1. 以 `pid_order` 逐一掃描,記錄每個「作者鍵」首次出現的序;作者鍵 = `user_id` 正規化後的字串,空/NULL → 特殊 sentinel(不明)。
2. 依作者鍵分組。
3. 每組內部以 `int(pid)` 降冪排序(非數字 fallback 字典序降冪)。
4. 輸出:已知作者依首次出現順序、各自 PID 降冪;最後接上不明桶(同樣 PID 降冪)。
5. `flat_order` = 各 batch 攤平串接。

性質:輸出為輸入的排列(set 不變),`len` 不變 → 進度計數 `_step4_pid_total` 不受影響。

### 新增 3 — 實例方法

`thread_download._reorder_pid_order_by_author(pid_order)`:用 `self._metadata_db.user_id_map_for_pids(pid_order)` 抓一次 map,呼叫 `compute_author_order`,回傳 `(flat_order, author_batches)`。並 emit 一則 log:有 N 筆作者不明已排到最後,提示重跑 Step 2/3 可補作者資料。

### 改 4 — `run()`

`app/core/thread_download.py:2356` 之後:

```python
pid_order, pid_groups = self._group_urls_by_pid(self.allurl)
if self.author_order:
    pid_order, author_batches = self._reorder_pid_order_by_author(pid_order)
else:
    author_batches = [pid_order]          # 單一 batch = 現行行為,零回歸
...
self._step4_pid_total = len(pid_order)
failed_nested = self._execute_downloads(pid_order, pid_groups, author_batches)
```

### 改 5 — 執行路徑

- `_execute_downloads(pid_order, pid_groups, author_batches)`:新增第三參數 `author_batches`,轉傳給 pool 路徑。
- `_execute_downloads_single(pid_order, pid_groups)`:**邏輯不變**,直接吃重排後的 `pid_order`,循序即嚴格。
- `_execute_downloads_pool(author_batches, pid_groups)`:改成逐 batch 處理 — 對每位作者:submit 該作者全部 PID 的 future → `as_completed` 收完該作者 → 才進下一位(barrier)。作者內部仍並行。`done` 計數跨 batch 累加。開關關閉時 `author_batches=[pid_order]` → 單一 batch → 與今日行為完全相同。

### 設定(1 個開關,預設關)

- 新鍵 `download.author_order: bool = False`(`app/core/settings_store.py` 的 `DEFAULTS["download"]`)。
- UI:`app/gui/views/settings_view.py` 在「依作者 ID 建立子資料夾」(`:91`)旁新增 `ft.Switch`,label:**「依作者順序下載(同作者連續)」**;`to_dict` 寫回 `download.author_order`(與 `tag_strip_brackets` 同 section,`:387-388`)。
- `app/gui/run_actions.py:_build_step4`(`:637` 旁)讀 `dl.get("author_order", False)` 傳進 `download_thread(...)`。
- `download_thread.__init__` 新增 keyword `author_order=False`,沿 `tag_strip_brackets` 既有路徑存 `self.author_order`。
- 作者之間 / 作者內部 / 不明處理 三項為固定行為,寫死在常數/函式,不開額外 UI。

## 邊界與風險

- **舊資料 `user_id` 多為 NULL**:全進不明桶排到最後;以 log 明示「N 筆作者不明,已排到最後;重跑 Step 2/3 可補」,避免被誤認為壞掉。
- **PID 鍵對齊**:`pid_order` 的 pid 來自 `_extract_pid_from_download_url`;查詢端以 `_coerce_pid` 對齊 `artworks.pid`;map 以原始 pid 為鍵 → 不會對不上。
- **pause/resume 安全**:重排只在執行前做一次,消費固定清單;暫停續跑不重洗。
- **失敗重排**:失敗 URL 在分組前已併入 `self.allurl`,因此一併進作者排序(與「嚴格作者順序」一致)。
- **pool barrier 代價**:作者交界處 pool 會排空再續,交界吞吐略降 — 選「嚴格」的明確成本。
- **`off` = 零回歸**:重排與 barrier 僅在開關開啟時生效。
- **使用者字串**:新 label / log 一律繁體中文(專案慣例)。

## 測試計畫

- `compute_author_order`(純函式):作者間=發現順序、作者內=PID 降冪、不明桶在最後、混合作者、空輸入、非數字 pid fallback。
- `user_id_map_for_pids`:插入後查詢、缺列回 None、分塊(>900)。
- pool barrier:用可控假下載函式記錄 submit 順序,斷言「作者 A 全收完才 submit 作者 B」。
- 接線:`_build_step4` 有傳 `author_order`;`settings_view.to_dict` round-trip。
- 回歸:`author_order=False` → `pid_order` 不變,既有測試續綠(`pytest -m 'not integration'`)。

## 不做的事(YAGNI)

- 不動 schema、不動 SQL、不改 views。
- 不對既有檔案做 retroactive 重排/改名。
- 不開「作者間 / 作者內 / 不明」三個額外下拉(用拍板固定值)。
- 不強制開啟 per-author 資料夾(獨立 `create_dir` 開關,與本功能正交)。
- 不在 pool 模式為「作者不明桶」做特別並行策略(比照一般作者,整桶一個 batch)。
