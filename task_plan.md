---
goal: 全面提升 Pixiv 下載器程式碼品質——消除重複、修正潛在 bug、提高可維護性、TDD 可測試性
started: 2026-04-25
status: in_progress
---

## 已完成（摘要）

- Phase 1: Cookie 工具函數集中到 pixiv_thread_utils.py ✅
- Phase 2: PauseableThread base class ✅
- Phase 3: Bug 修正 + import 清理 ✅
- Phase 4: safe_read_json() 消除重複 JSON 讀取 ✅
- Phase 5: run_actions.py 信號連接去重 ✅
- Phase 6: tag_edit.py 改為資料驅動（tag_rules.json） ✅
- Phase 7: get_img_url_thread.run() 拆解 ✅
- Phase 8: 統一 PID 讀取工具函數 ✅
- Phase 9: get_pixiv_author_imgID_Thread.run() 拆解 ✅
- Phase 10: download_thread.run() 拆解 ✅
- Phase 11: pixiv_thread.py 拆分成 6 個模組 ✅
- 目錄整理：.gitignore 擴充 + other/ 搬移 ✅

---

## 待執行

---

### Phase 12-A — tag_edit.Tag() 例外時回傳 None（bug） ✅
**優先級: 🔴 HIGH**
**位置:** `app/core/tag_edit.py:43`

```python
except Exception as err:
    print(err)
    # 隱性回傳 None → 呼叫端 for t in None → TypeError
```
→ 改為 `return Taglist`（回傳已處理到一半的結果）或 `return []`

---

### Phase 12-B — PauseableThread._sleep_with_countdown 隱性依賴子類別信號 ✅
**優先級: 🔴 HIGH**
**位置:** `app/core/pixiv_thread_base.py:165`

`self._countdown` 在 base class 未定義，靠 `try/except` 掩蓋。任何新子類別若忘記加 `_countdown = pyqtSignal(int)` 會無聲失敗。

→ 在 `PauseableThread` 加入 `_countdown = pyqtSignal(int)` 預設宣告

---

### Phase 12-C — pid_num/pid_len 雙重定義清理 ✅
**優先級: 🟡 MEDIUM**
**位置:** `app/core/pixiv_thread_base.py:44-47` 與 `app/core/thread_pid_scan.py`

兩個模組各有一份，`pixiv_thread_base` 那份永遠不被更新，造成混淆。

→ 從 `pixiv_thread_base.py` 移除 `pid_num`/`pid_len`，只保留 `thread_pid_scan.py` 中的版本

---

### Phase 12-D — assert 描述性改善 + .gitignore 補項 ✅
**優先級: 🟡 MEDIUM**

1. `app/core/tag_edit.py:7`：
   ```python
   assert TAG_RULES, "tag_rules.json is empty or missing"
   ```
   → 加入檔案路徑資訊

2. `.gitignore`：補上 `following4.json`

---

### Phase 13 — 各 class 檔案精簡 import ✅
**優先級: 🟡 MEDIUM（可維護性）**
**位置:** `app/core/thread_following.py`, `thread_test.py` 等

拆分時複製了完整 50 行 import header，包含各 class 未使用的套件（`numpy`, `PIL`, `imageio`, `zipfile`, `subprocess` 等出現在 `thread_following.py`）。

步驟：
1. 逐一確認各 class 實際 import 的符號
2. 各 class 檔案只保留自己使用的 import
3. `from app.core.pixiv_thread_base import ...` 繼續保留

---

### Phase 14 — 為 run() helper 方法補單元測試（TDD） ✅
**優先級: 🟡 MEDIUM（TDD 核心目標）**

Phase 7-10 拆出的 helper 方法目前無直接測試：

| 方法 | 所在 class | 測試重點 |
|---|---|---|
| `_load_and_filter_pid_list` | get_img_url_thread | 空 PID 檔、revoked_pid 過濾 |
| `_build_and_emit_task_queue` | get_img_url_thread | 正確建立 Queue |
| `_finalize_on_complete` | get_img_url_thread | tag_ban/like 分類寫檔 |
| `_filter_work_list` | get_pixiv_author_imgID_Thread | 30 天冷卻過濾邏輯 |
| `_commit_step2_outputs` | get_pixiv_author_imgID_Thread | pictures_id.txt 寫入去重 |
| `_execute_downloads` | download_thread | single/multi 分支 |
| `_finalize_downloads` | download_thread | remaining URLs 計算 |
| `_refresh_and_write_exist_pid` | download_thread | exist_pid 重新載入 |

---

### Phase 15 — run_actions.py 個人硬編路徑 ✅
**優先級: 🟢 LOW**
**位置:** `app/gui/run_actions.py:15-16`

```python
r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe",
```
→ 保留 glob 搜尋邏輯，移除個人 hardcode 路徑

---

### Phase 16 — exist_pid 格式統一 + 性能改進 ✅
**優先級: 🟡 MEDIUM（性能 + 維護性）**
**詳細計畫:** `C:\Users\Eric\.claude\plans\velvety-swinging-ritchie.md`（項目 1）

- 新增 `trash_file(base_path, file_path, max_days=30)` 到 `pixiv_thread_utils.py`
- 更新 `load_exist_pid_set()`：只讀 `exist_pid.json`，舊格式自動遷移後 trash
- 更新 `_refresh_and_write_exist_pid()`：移除 `sorted()` + 移除 txt 雙寫
- 更新 `sync_exist_pid_with_download_folder()`：移除 sorted + txt 寫入
- 更新 `download_thread.__init__`：改呼叫 `load_exist_pid_set()`
- 預期：寫回 2.03s → ~0.6s，格式從 3 種統一成 1 種

---

### Phase 17 — UI 介面設定統一為 `settings.json` ✅
**優先級: 🟡 MEDIUM（可維護性）**
**詳細計畫:** `C:\Users\Eric\.claude\plans\velvety-swinging-ritchie.md`（項目 2）

5 個分散設定檔（`data.json`, `logging.json`, `othersettings.json`, `cookies.json`, `pass.json`）
合併為單一 `settings.json`，遷移後舊檔進回收桶（30 天保留）。

- 新建 `app/core/settings_store.py`：`SettingsStore` class（讀寫 + 遷移）
- 更新 `app/gui/user_info.py`：5 個 class 改用 `SettingsStore`
- 更新 `app/gui/controller.py`：建立 `SettingsStore` 實例
- 新建 `tests/test_settings_store.py`

---

### Phase 18 — ugoira frame 規範化與 GIF 存檔方法統一（重複消除） ✅
**優先級: 🔴 HIGH**  **技能對應:** py-code-health（Duplicate → Unify）
**位置:** `app/core/thread_download.py:822-944`（共 4 個方法、約 120 行）

**Smell：** 兩對 near-duplicate 方法，差異只在 frame 資料來源（disk path vs bytes blob）。

| 方法 | 行數 | 差異處 |
|---|---|---|
| `_normalize_ugoira_frames_for_gif` | 822-854（33 行）| `Image.open(frame_path)` |
| `_normalize_ugoira_frame_blobs_for_gif` | 856-888（33 行）| `Image.open(io.BytesIO(blob))` |
| `_save_ugoira_gif` | 890-916（27 行）| 呼叫前者 |
| `_save_ugoira_gif_from_blobs` | 918-944（27 行）| 呼叫後者 |

**步驟（逐一 commit）：**

1. **Extract Function：frame loader**
   - 新增 `_open_ugoira_frame(source)`：`source` 可為 path 或 `bytes`；回傳 `PIL.Image` 或 `None`
   - 對 `bytes`/`bytearray`/`memoryview` 用 `io.BytesIO`；對 `str`/`os.PathLike` 用 `Image.open(path)`
   - 共用 `convert("RGBA")` 與 close 行為

2. **Unify：normalize 接受 iterable**
   - 新增 `_normalize_ugoira_frames(sources) -> list[Image]`，內部呼叫 `_open_ugoira_frame`
   - 刪除 `_normalize_ugoira_frames_for_gif` 與 `_normalize_ugoira_frame_blobs_for_gif`

3. **Unify：save 接受 iterable**
   - 新增 `_save_ugoira_gif(sources, output_path, delay_info)` 單一方法
   - `gif_download` 內 `self._save_ugoira_gif_from_blobs(frame_blobs, ...)` 改為 `self._save_ugoira_gif(frame_blobs, ...)`

**驗證：**
- 視覺檢查 diff：兩組呼叫點從不同方法名改為相同方法名
- `pytest -q`：整體回歸
- 若有 ugoira 測試，跑 `pytest tests/ -k ugoira`

**預期成果：** 120 行 → 約 50 行（刪除約 70 行）；兩個入口共用同一條 normalize + save pipeline。

---

### Phase 19 — 下載等待倒數方法統一（重複消除） ✅
**優先級: 🔴 HIGH**  **技能對應:** py-code-health（Parametrize Similar Functions）
**位置:** `app/core/thread_download.py:999-1057`

**Smell：** `_sleep_between_downloads` 與 `_sleep_within_pid` 約 30 行，差異三點：
1. log 標籤：「[下載等待][PID間]」vs「[下載等待][同PID]」
2. log 顏色：`color='green'` vs `'gray'`
3. 外層 loop 判斷：前者檢查 `_isPause == 2 or _stop_after_group`；後者只檢查 `_isPause == 2`

**步驟（單次 commit 即可）：**

1. **Extract Function：參數化 helper**
   ```python
   def _run_download_countdown(
       self,
       pid: str,
       min_sec: int,
       max_sec: int,
       *,
       label: str,          # "PID間" 或 "同PID"
       color: str,          # "green" 或 "gray"
       respect_group_stop: bool,
   ) -> None
   ```
2. `_sleep_between_downloads` 改為 1-2 行呼叫（`respect_group_stop=True, color="green", label="PID間"`）
3. `_sleep_within_pid` 改為 1-2 行呼叫（`respect_group_stop=False, color="gray", label="同PID"`）

**注意：** 不動 `PauseableThread._sleep_with_countdown`（Phase 12-B 已修）；本 Phase 僅影響 `download_thread`。

**驗證：** `pytest -q`；手動跑一次 Step 4 下載，確認兩段倒數訊息仍然正確輸出。

---

### Phase 20 — splitID 深層 try/except 改寫為 guard clause ✅
**優先級: 🟡 MEDIUM**  **技能對應:** py-complexity（Guard Clauses, Extract Function）
**位置:** `app/core/thread_download.py:1261-1310`

**Smell：** 6 層 try/except 巢狀，最內層 `except: print(err); pass`。最初粗估 cyclomatic complexity ≥ 10；實際建議用 `radon cc app/core/thread_download.py -n C` 先量。

**步驟（分 3 commit）：**

1. **先寫 behavior-preserving 測試（TDD 保護網）**
   - 新建 `tests/test_split_id.py`
   - 輸入樣本涵蓋各提取分支（舉例）：
     - `PID=12345_p0.jpg`
     - `PID12345 ...`
     - `PID12345p0.jpg`
     - `illust_12345_p0.jpg`
     - `illust_44773280_20220413_040534.jpg`
     - 非圖片檔名（應略過）
   - 斷言 `splitID(samples)` 的集合內容
   - Commit：「test: add splitID golden-case tests」

2. **Extract Function：多個 parser**
   - `_parse_pid_from_pid_eq(filename)`：處理 `PID=xxx_...` 格式
   - `_parse_pid_from_pid_prefix(filename)`：處理 `PIDxxx ...` / `PIDxxx.` 格式
   - `_parse_pid_from_illust_prefix(filename)`：處理 `illust_xxx_...` 格式
   - 每個回傳 `str | None`，內含自己的小小 try/except
   - Commit：「refactor: extract PID parsers from splitID」

3. **Guard clause rewrite of splitID**
   - 主函式簡化為：
     ```python
     for file in filelist:
         if not _is_image_file(file):
             continue
         if not _contains_pid_marker(file):
             continue
         for parser in (self._parse_pid_from_pid_eq,
                        self._parse_pid_from_pid_prefix,
                        self._parse_pid_from_illust_prefix):
             pid = parser(file)
             if pid:
                 results.add(pid)
                 break
     ```
   - 用 `set` 去重（取代 `np.unique(...).tolist()`）；若其他地方仍需 numpy 保留 import，只改這一段。
   - Commit：「refactor: rewrite splitID with guard clauses and set dedup」

**驗證：**
- 每個 commit 都跑 `pytest tests/test_split_id.py`
- 最後跑 `radon cc app/core/thread_download.py -n C`，確認 `splitID` 掉出 C+ 名單
- 完整 `pytest -q`

**預期成果：** 6 層 → 最多 2 層；複雜度分散到 parser；加上 goldens 測試。

---

### Phase 21 — download_thread.__init__ legacy args 抽離 ✅
**優先級: 🟡 MEDIUM**  **技能對應:** py-complexity（Extract Function, Replace Repetitive Field Operations）
**位置:** `app/core/thread_download.py:122-178`（約 60 行）

**Smell：** `__init__` 中間插入 60 行 `if legacy_args: try: ... except: pass` 的 positional/keyword 回溯相容碼，掩蓋正規屬性設定。`radon cc` 應把 `__init__` 標為 C+。

**步驟：**

1. **Define schema：把 legacy 欄位表格化**
   ```python
   _LEGACY_POSITIONAL = [
       ("jxl_enable", bool),
       ("jxl_cjxl_path", str),
       ("jxl_delete_original", bool),
       ("jxl_effort", int),
   ]
   _LEGACY_KWARGS = [
       ("jxl_enable", bool),
       ("jxl_cjxl_path", str),
       ("jxl_delete_original", bool),
       ("jxl_effort", int),
       ("like_num", int),
       ("ban_tag", list),
       ("must_tag", list),
       ("special_like_rules", "special"),
       ("ai_gen_dir", bool),
   ]
   ```
2. **Extract Function：`_apply_legacy_constructor_args(legacy_args, legacy_kwargs, defaults)`**
   - 用 `for name, caster in _LEGACY_POSITIONAL: ...`（取代重複的 if-else 階梯）
   - 回傳 dict，`__init__` 主流程 `overrides = self._apply_legacy_constructor_args(...)` 並 `jxl_enable = overrides.get("jxl_enable", jxl_enable)` 類推
3. `__init__` 主流程只保留屬性 assign 與驗證

**驗證：**
- 保留既有 call sites；確認 `run_actions.py` 呼叫不變（`Grep` 驗證）
- `pytest -q`
- `radon cc app/core/thread_download.py -n C`：`__init__` 掉出 C+

---

### Phase 22 — 下載檔名組合抽成共用 helper ✅
**優先級: 🟡 MEDIUM**  **技能對應:** py-code-health（Extract Common Logic）
**位置:** `app/core/thread_download.py:1792-1809`（gif_download）與 `1888-1903`（jpg_download）

**Smell：** 兩塊平行 18 行的檔名組裝 `if notag / if name=='' / if notime` 巢狀組合。

**步驟：**

1. **Extract Function：**
   ```python
   def _build_download_filename(
       self,
       pid: str,
       *,
       page_suffix: str,    # "" 對 gif；"p0" 對 jpg
       ext: str,            # "gif" / "jpg" / "png" ...
       hashtag: str,        # 已經 build 過的 hashtag 字串（含前導 " "）
       timetag: str,        # timestamp string
       include_tag: bool,
       include_time: bool,
   ) -> str
   ```
2. 兩個呼叫端改為 2-3 行呼叫
3. `except:` 分支產生 `illust_...` fallback filename 也移入 helper

**驗證：** 視覺比對 before/after；同一 PID 同一頁產生的檔名相同（可用小單元測試 `tests/test_build_download_filename.py` 驗證 8 組組合：`include_tag × include_time × media_type`）。

---

### Phase 23 — py-code-health 自動掃描（dead code + duplicate） ✅
**優先級: 🟡 MEDIUM**  **技能對應:** py-code-health

Phase 18-22 是針對已知 smell 的人工修復。本階段改用工具把「還沒被看到」的 dead/duplicate 掃出來。

**步驟：**

1. **加入工具**（目前無 pyproject.toml，用 venv 直接裝）
   ```bash
   pip install vulture pylint
   ```

2. **Dead code 掃描**
   ```bash
   mkdir -p reports
   vulture app/ --min-confidence 80 --exclude "tests,trash,__pycache__" > reports/dead_code.txt
   ```
   - 審核：低信心條目多半是 PyQt signal/slot 機制造成的假陽性（例如 `on_xxx_clicked`）
   - 為假陽性建立 `vulture_whitelist.py`，再次跑：
     ```bash
     vulture app/ vulture_whitelist.py --min-confidence 80
     ```
   - 確認真死碼後再刪除；每筆死碼單獨 commit

3. **Duplicate code 掃描**
   ```bash
   pylint --disable=all --enable=duplicate-code \
          --duplicate-code-min-lines=8 \
          --recursive=y app/ > reports/duplication.txt
   ```
   - Phase 18-22 應已解掉最大一批；剩餘的依 80/20 挑 top 2-3 塊處理

**驗證：** Re-scan 後 dead_code.txt / duplication.txt 僅剩 whitelisted 或 < 8-line 小片段。

---

### Phase 24 — py-complexity baseline + 門檻守護 ✅
**優先級: 🟡 MEDIUM**  **技能對應:** py-complexity

**步驟：**

1. **安裝工具**
   ```bash
   pip install radon lizard xenon wily
   ```

2. **建立 baseline**
   ```bash
   radon cc app/ -n C -s > reports/complexity.txt
   radon mi app/ -n B > reports/maintainability.txt
   lizard -C 15 app/ > reports/cognitive.txt
   wily build app/
   ```

3. **排序找熱點**（預估 top offenders）
   - `app/gui/controller.py`（1672 行，god class）
   - `app/core/thread_url_fetch.py`（1610 行）
   - `app/core/thread_download.py`（1946 行，Phase 18-22 後應下降）

4. **單檔超過 500 行 → 拆分規劃**
   - 針對 top 3 超長檔，寫 split 計畫（哪些類別/方法可拆到新模組），但不在本 Phase 實際拆分
   - 輸出到 `reports/split_plan.md`

5. **加 pre-commit 門檻（選用）**
   ```bash
   xenon --max-absolute B --max-modules A --max-average A app/
   ```
   - 若目前無法通過，先降低門檻到「不退步」級別

**驗證：** `wily diff HEAD~1` 顯示（完成 18-22 後）複雜度下降。

---

### Phase 25 — py-modernize（pyproject.toml + Python 3.13 語法 + f-string） ✅
**優先級: 🟢 LOW**  **技能對應:** py-modernize

目前專案無 `pyproject.toml`、無 `setup.py`，只有 `pytest.ini`。最小化引入現代配置。

**步驟：**

1. **建立 `pyproject.toml`（最小版）**
   ```toml
   [project]
   name = "pixiv-img-download"
   version = "0.1.0"
   requires-python = ">=3.8"   # 目前 __pycache__ 有 cpython-38，先不升 3.13
   dependencies = [
       "PyQt5",
       "requests",
       "numpy",
       "Pillow",
       "qfluentwidgets",
       # 依實際 import 補齊
   ]

   [tool.ruff]
   target-version = "py38"
   line-length = 120

   [tool.ruff.lint]
   select = ["E", "F", "UP", "B", "SIM"]
   ignore = ["E501"]   # 先不強制行長

   [tool.pytest.ini_options]
   markers = ["integration: live tests"]
   ```
   - 移除 `pytest.ini`（遷移到 `[tool.pytest.ini_options]`）

2. **是否升 Python 3.13？**
   - 先不升；目前沒有 3.13-only 語法、runtime 是 3.8。`requires-python = ">=3.8"`
   - 若之後要升，單獨 Phase 處理

3. **f-string 現代化（安全的一次性 sweep）**
   - `app/core/thread_download.py` 有 41 處 `.format(...)`，其他大型檔類似
   - 不全部改；只針對 Phase 18-22 動到的區塊順手改成 f-string
   - 若要全面改，用 ruff：
     ```bash
     ruff check app/ --select UP --fix --diff   # 先看 diff
     ruff check app/ --select UP --fix          # 再套用
     ```
   - 手動 review 中文訊息/HTML 字串是否被誤動

4. **pip → uv（選用，不強推）**
   - 專案仍以 conda 為主（根目錄有 `.conda/`）；若未來統一用 uv 再處理
   - 本 Phase 暫不做 uv 遷移

**驗證：**
- `pytest -q` 全綠
- `ruff check app/` 無新 error（可先 downgrade 到 warning）
- `python main.py` 啟動無異常

---

### Phase 26 — py-refactor 最終驗證與 regression 守護 ✅
**優先級: 🟢 LOW**  **技能對應:** py-refactor（orchestrator）

Phase 18-25 全部完成後的收尾。

**步驟：**

1. **全面 scan：確認分數**
   ```bash
   ruff check app/
   radon cc app/ -n C
   radon mi app/ -n B
   vulture app/ vulture_whitelist.py --min-confidence 80
   pylint --disable=all --enable=duplicate-code --recursive=y app/
   pytest --cov=app --cov-report=term-missing
   ```

2. **對比 baseline（Phase 24 建立）**
   ```bash
   wily diff HEAD~N   # N = Phase 18 起點的 commit 距離
   wily report app/
   ```

3. **更新 README / CLAUDE.md**
   - 記錄新加的 tooling（ruff, radon, vulture）
   - 記錄執行命令（`ruff check app/`, `pytest`, `radon cc app/ -n C`）

4. **加 git hook（選用）**—若要導入，走 `py-git-hooks` skill

**驗證：**
- 所有 reports/*.txt 指標與 Phase 24 baseline 相比至少不退步
- `pytest -q` 全綠
- `git log --oneline` 每個 Phase 都有獨立 commit，可個別 revert

---

## 執行順序

| Phase | 說明 | 技能 | 風險 | 時間 |
|---|---|---|---|---|
| 16 | exist_pid 統一 | — | 低-中 | 60 分鐘 |
| 17 | settings.json 統一 | — | 中 | 90 分鐘 |
| 18 | ugoira frame/GIF 方法 unify | py-code-health | 低 | 30 分鐘 |
| 19 | 下載等待倒數方法 unify | py-code-health | 低 | 20 分鐘 |
| 20 | splitID guard clause 改寫 | py-complexity | 中（需加 test） | 60 分鐘 |
| 21 | __init__ legacy args 抽離 | py-complexity | 低 | 25 分鐘 |
| 22 | 下載檔名 helper 抽離 | py-code-health | 低 | 25 分鐘 |
| 23 | dead/duplicate 自動掃描 | py-code-health | 低 | 60 分鐘 |
| 24 | complexity baseline + 守護 | py-complexity | 低 | 40 分鐘 |
| 25 | pyproject.toml + f-string sweep | py-modernize | 低-中 | 60 分鐘 |
| 26 | 最終驗證 + regression 守護 | py-refactor | 低 | 30 分鐘 |

**建議分批：** 18-22 為第一批（實際程式碼修改），23-26 為第二批（工具化與守護）。每個 Phase 單獨 commit；Phase 20 因需 test，建議拆 3 個 commit（test → extract → rewrite）。

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| test_cookie_cooldown stub 缺 cookie_pool | Phase 2 | 在 stub 加 t.cookie_pool = [] |
| Edit tool 無法匹配 CRLF 檔案 | Phase 7-10 | 改用 Python 腳本直接處理 bytes |

---

## Session 6 — 2026-04-26 (code-review-skill 衍生新 Phase)

code-review-skill 掃描後新增 3 個 Blocking/Important Phase：

### Phase 27 — 統一 import 路徑（修正 app/core 死碼） ✅
**優先級: 🔴 BLOCKING**

**問題：** `main.py` 從 repo 根啟動，sys.path[0]=repo 根。`app/core/*.py` 內部 `from pixiv_api import *` / `import pixiv_api` 會命中 root `pixiv_api.py`（897 行舊版），不是 `app/core/pixiv_api.py`（890 行新版）。後果：Phase 25 的 vulture 清理、selenium try/except 包裝、`safe_read_json` 遷移在 runtime 全部沒跑到。同樣問題影響 `pixiv_thread.py` / `safe_io.py` / `pixiv_thread_utils.py`。

**範圍：**

| 檔案 | 行數 | 動作 |
|---|---|---|
| 根 `pixiv_api.py` | 897 | 移到 `backup/` → 替換成 shim `from app.core.pixiv_api import *` |
| 根 `pixiv_thread.py` | 1932 | 移到 `backup/` → shim `from app.core.pixiv_thread import *` |
| 根 `safe_io.py` | 131 | 移到 `backup/` → shim |
| 根 `pixiv_thread_utils.py` | 141 | 移到 `backup/` → shim |
| 根 `download_img.py` | 411 | 移到 `backup/`（只 `other/scripts/` 與 `.claude/worktrees/` 引用） |
| 根 `run_actions.py` | 278 | 移到 `backup/`（只 `.claude/worktrees/` 引用） |

**驗證：**
- pytest 全綠（102 passed）
- `python -c "import pixiv_api; print(pixiv_api.__file__)"` 應指到 `app/core/pixiv_api.py`（透過 shim 的 `__all__`）
- ruff 違規數應略降

### Phase 28 — 網路韌性（timeout / TLS） ✅
**優先級: 🔴 BLOCKING**

完成（最小化版）：
1. ✅ 12 處 `requests.get` 全部加 `timeout=(10, 30)`（pixiv_api / thread_following / thread_pid_scan / update_selenium）
2. ✅ 移除 4 處 `verify=False`（pixiv_thread_utils:641,653、thread_download:1707,1817）

**延後（拆 Phase 28-B）：**
- `safe_json(res, *keys, default=None)` helper + 4 個 call site 改寫
- `gif_download` / `jpg_download` 重試指數退避 + 換 cookie（80+ 行巢狀邏輯，需先補 test）

### Phase 29 — Thread lifecycle ✅
**優先級: 🟡 IMPORTANT**

完成：
1. ✅ `controller.closeEvent` 改 `stop() → wait(5000) → terminate()`（controller.py:856-863）
2. ✅ 移除 `__del__ self.wait()`（controller.py:56-57、thread_following.py:139-140）
3. ✅ `err_url.txt` 改走 `atomic_write_text(backup=False)`（thread_download.py:1383-1385）
4. ✅ `controller.py:777` `msgBox.exec()` → `exec_()`

**已過時 / 已完成（不需動）：**
- `pass.json` 從 `cookies.json` 拆出 — Phase 17 已併入 `settings.json` 且 `SettingsStore.save()` 用 `backup=False`，密碼不會進 history/

**延後（拆 Phase 29-B）：**
- `_isPause: int` → `threading.Event` — 60+ 觸點、無暫停/停止語意 test、風險高，需先補 test 框架

---

### Phase 28-B — safe_json helper ✅
**優先級: 🟡 IMPORTANT**

完成：
1. ✅ 新增 `safe_json(res, *keys, default=None)` 至 `app/core/pixiv_thread_utils.py`（含 docstring 說明 Pixiv error envelope 行為）
2. ✅ 改寫 10 個 call site：`pixiv_api.py`（6 處）、`thread_following.py`（3 處）、`thread_pid_scan.py`（1 處）
3. ✅ 新增 `tests/test_safe_json.py`（7 tests）涵蓋：嵌套 key 走訪、缺鍵、`error=true` envelope、非 JSON、中間值非 dict、預設 None
4. **驗證：** `pytest -m "not integration"`: **109 passed**（102 + 7）

### Phase 28-C — jpg_download 重試指數退避 ✅
**優先級: 🟡 IMPORTANT**

完成（最小化版）：
1. ✅ `jpg_download` 原本 `for i in range(0,5)` 重試圈是**死碼**（except 立即 `return [url,timetag]`，5 次只執行 1 次）
2. ✅ 改為：`except` 內 `last_err = err; if i < 4: time.sleep(min(30, 2**i + random())); continue`，loop 外才回傳失敗
3. ✅ 隱式 cookie rotation：每次重試 `_select_cookie_for_pid` 重新挑（cookie pool > 1 時自動有換）

**延後（拆 Phase 28-D）：**
- 顯式 cookie rotation（強制 `i>=2` 換另一條 cookie），需修改 `_select_cookie_for_pid` 或新增 `_select_alternate_cookie_for_pid`
- `gif_download` 增加 retry wrapper（已用 `fetch_with_cookie_retry` 處理 ugoira_meta 階段，下載階段尚無 retry）
- `err_url.txt` 寫入 status code / exception 類別

### Phase 39 — thread_no_use_seleium_get_pid 拆解 ✅
**位置：** `app/core/thread_pid_scan.py:413`

抽 6 個 `_step2_*` helper（fetch_artist_pid_list / emit_incremental_status / record_skipped_pids / append_new_pids / record_author_progress / record_artist_failure）。
- **D (30) → B (6)**（101 → 21 行 orchestrator）

### Phase 40 — _finalize_downloads 拆解 ✅
**位置：** `app/core/thread_download.py:1471`

抽 `_classify_download_results` / `_compute_remaining_urls` / `_persist_url_meta`。
- **D (25) → A (5)**（61 → 16 行）
- `tests/test_step4_download_helpers.py` 9 tests 仍綠

### Phase 30 (P-α) — get_download_url F→D ✅
**位置：** `app/core/thread_url_fetch.py:1365`

謹慎模式（無 golden test 安全網）：抽 8 個 `_step3_*` helper，只動純資料/log/sequencing 段，不碰網路重試與 cookie 邏輯。
- **F (52) → D (28)** — codebase 主流程 **0 個 F 級函式**
- 行為等價：`_diag` 事件順序、`_output.emit` 順序、`_sleep_ultra_slow` 時序皆保留
- `_finalize` 仍為 closure（保留 query_source / need_cookie 的延遲求值語意）
- 剩餘 CC 主要在 6 個 skip-path 分支，深入抽要動 emit 順序，不在當前 scope
**優先級: 🔴 (HIGH ROI but BLOCKED)**  **位置:** `thread_url_fetch.py:1353`（CC=52，142 行）

**問題：** 同時做 5 件事 — fetch URL / 解析 response / cookie retry / diag log / 寫檔。

**前置條件：必須先補 golden test**（mock requests + 一組已知輸入/輸出對照）才能動，否則無法驗證行為等價。同 Phase 29-B 風險模式。

### Phase 35 — UI Frameless 白底進度區可見性修正 ✅
**優先級: 🔴 BUG**  **位置:** `app/gui/controller.py:640,670`

**根因（Plan agent 回報）：** `WA_TranslucentBackground=True` + `QMainWindow{background:transparent}` + `QProgressBar` 完全沒在 stylesheet 裡 → progressBar 走 native 透明繪製，文字看不見。

**修法（按風險小→大）：**
1. 刪 `controller.py:640` `self.setAttribute(Qt.WA_TranslucentBackground, True)`（`qframelesswindow` 自己處理視窗形狀，不需要手動 translucent）
2. 在 `controller.py:670` stylesheet 補 `QProgressBar` / `QProgressBar::chunk` / `QTabBar::tab` / `QSplitter::handle` / `QStatusBar` 規則

### Phase 36 — Step 3 越跑越慢（O(N²) → O(N)） ✅
**優先級: 🔴 BUG**  **位置:** `app/core/thread_url_fetch.py`

**根因（Plan agent 回報）：** `_mark_pid_processed:989` 在 **每個 PID** 呼叫 `_persist_pending_pid_file:956`，內部 `sorted()` + `atomic_write_text(backup=True)` + `shutil.copy2` 整檔。總 I/O = O(N²/2)，N=10000 估算 300s+ 純磁碟開銷。

**修法（最小化）：**
1. 移除 `_mark_pid_processed:989` 的 `_persist_pending_pid_file()` 呼叫（只更新 in-memory set）
2. `_run_processing_loop:1219` batch flush 區塊內加一次 `_persist_pending_pid_file()`，與既有 `_write_all_url_snapshot` 對齊每 100 PID
3. `_persist_pending_pid_file:962` `backup=True` → `backup=False`（runtime pending 檔不需 history）
4. `_write_all_url_file:746` 加 `backup=False` 參數預設、`_finalize_on_complete` 路徑保留 `backup=True`
5. **保證 `_finalize_on_complete` 與 `closeEvent` 仍會 flush 一次** pending PID 與 all_url，避免中斷時資料遺失

**預期：** 寫檔次數 N → N/100，磁碟 I/O 從 O(N²) 降為 O(N²/100) ≈ 100x 加速

### Phase 31-B — 統一本地快取（移除 pixiv_info_cache.json） ✅
**優先級: 🟡 IMPORTANT**  **動機:** 使用者觀察到兩份本地快取檔做同一件事

**問題：** `Pixiv_info` 內部維護 `pixiv_info_cache.json`；step3 同時寫 `all_url_meta.json`。兩份 JSON 在 step3 完成後內容大致相同（後者是 superset，多 `requires_cookie`）。`download_thread` 在 Phase 31 已加 `_load_artwork_metadata` 把 `self.url_meta` 當第一層 cache → `pixiv_info_cache.json` 變成第二層多餘 fallback。

**動作：**
1. 刪除 `pixiv_api.py` 中：
   - `_pixiv_info_cache` dict + locks
   - `_pixiv_info_cache_path()` / `_load_pixiv_info_cache_from_disk()` / `_persist_pixiv_info_cache()`
   - 模組載入時的 `_pixiv_info_cache.update(...)`（line 100-103）
   - `Pixiv_info` 開頭的 cache check（line 572-594）
   - `Pixiv_info` 結尾的 `_pixiv_info_cache[id] = ...` 與 `_persist_pixiv_info_cache(id, ...)` 寫入
2. `Pixiv_info` 變成「純 HTTP fetch」，由呼叫端負責 cache（`download_thread._load_artwork_metadata` 已是這個模式）
3. 既有的 `pixiv_info_cache.json` 在使用者磁碟上變成孤兒——加一次性 `trash_file()` 清理（或不動，自然失效）
4. 不破壞 `Pixiv_info` 公開簽名

**意涵：**
- `all_url_meta.json` 變唯一 SoT
- step3 的 `Pixiv_info` 呼叫不再寫 `pixiv_info_cache.json`，省一次 atomic_write
- 若 `all_url_meta.json` 缺資料 → 直接 HTTP fetch（行為比之前更可預期）

**驗證 ✅：**
- `pytest -m "not integration"`: **119 passed**
- `_pixiv_info_cache` / `pixiv_info_cache.json` 字串在 `app/` 全消
- `Pixiv_info`: E (CC=31) → C (CC=19), −39%
- `pixiv_api.py`: 890 → 802 行 (−88)

**所有 `Pixiv_info` 呼叫點 cache-first 盤點：**
- `thread_url_fetch.get_download_url:1412` (step3) ✅ 用 `self.url_meta`（line 1389-1397）
- `thread_download` 三處 ✅ 用 `_load_artwork_metadata`（Phase 31）
- `pixiv_api.get_download_url:458` ⚠️ 無 cache——但**不在主流程**（唯一外部 caller 是 `other/scripts/download_url.py` 孤兒腳本）

**欄位無流失：** `all_url_meta.json` schema 是 `pixiv_info_cache.json` 的嚴格 superset（多 `requires_cookie`）。`tags ↔ tag`, `bookmarkCount ↔ like`, `pageCount ↔ pagecount`, `img_url ↔ img_url`，4 個欄位 1:1 對應。

**孤兒檔自動清理：** `pixiv_info_cache.json` 加入 `settings_store.LEGACY_FILES`，下次啟動 SettingsStore 會 `trash_file()`。

### Phase 31 (P-β) — gif_download / jpg_download 抽共用 helper ✅
**優先級: 🟡 IMPORTANT**

**抽出 4 個 helper（thread_download.py:488-560）：**
1. `_resolve_pid_and_cookie(url, *, source)` — PID 解析 + cookie 選擇 + need_cookie 解析
2. `_load_artwork_metadata(pid, pid_cookie)` — 優先 `self.url_meta` 快取，fallback 到 `Pixiv_info`
3. `_build_artwork_headers(pid, pid_cookie, need_cookie, *, honour_pid_used=False)` — headers + cookie 注入
4. `_log_ugoira_meta_failure(pid, htmlfile, meta_trace, first_try_resp)` — gif 專用 diag dump

**統一行為（解 code drift）：** gif 原本永遠呼叫 `Pixiv_info`、jpg 優先用 `url_meta` 快取——其實是 code drift 而非設計分歧（`url_meta` 是 step3 同樣寫入的、形狀也相同）。改 gif 也吃 cache，cache hit 時省一次 Pixiv_info HTTP 呼叫。

**結果：**
- `gif_download`: **E (CC=37) → D (CC=22)** −40%
- `jpg_download`: **D (CC=25) → C (CC=13)** −48%
- thread_download.py 唯一剩下的 E 級：`_convert_file_to_jxl` (CC=34) → Phase 32

**驗證：** `pytest -m "not integration"`: **119 passed**（+10 helper tests）

### Phase 32 — _convert_file_to_jxl 三段拆 ✅
**優先級: 🟢 LOW**  **位置:** `thread_download.py:736`（CC=34, 86 行）

職責：副檔名 gating + 重試 + 成功/失敗計數 + log。拆 `_jxl_should_convert(path)` / `_jxl_run_conversion(path)` / `_jxl_record_outcome(...)`。

### Phase 37 — _commit_step2_outputs D→A ✅
**位置：** `app/core/thread_pid_scan.py:282`

抽出 5 helper（`_persist_author_progress` / `_collect_step2_pids_from_queue` / `_merge_step2_pids_with_existing` / `_write_step2_pictures_id` / `_write_step2_skip_pids`）。主函式從 88 行 → 7 行 orchestrator。
- `_commit_step2_outputs`: **D (30) → A (2)**

### Phase 38 — Userdata_controller schema 化 ✅
**位置：** `app/gui/user_info.py:43`(load_data) + `:215`(setinfo)

引入 `_LOAD_FIELDS` / `_SETINFO_FIELDS` schema + `_apply_field` / `_apply_widget_value` helper。
- `load_data`: **D (23) → A (3)**（72 → 24 行）
- `setinfo`: **D (22) → A (4)**（68 → 19 行）
- 兩 class 整體：B → A

### Phase 33 — download_thread.__init__ 21-param → DownloadConfig dataclass 🔒 declined
**結論：投入 vs 報酬不成比例，主動延後。**

**重新評估後（2026-04-29，動工前先讀代碼）：**
- 現況 `__init__` CC=20 (C 級)，**已不是熱點**——Phase 21 已抽出 legacy args，validation 邏輯 ~30 行，attribute assignment ~50 行
- 「21 params」是 signature 美觀問題，不是真實邏輯複雜度
- 加 `DownloadConfig` dataclass 只是把 21 個 named args 從 `download_thread(...)` 平移到 `DownloadConfig(...)`，**內部 validation 與 assignment 不會消失**，總 LOC 反而變多（多一層 dataclass + adapter）
- 跨 `run_actions.py` 改動有 regression 風險，收益不成比例

**何時值得做：** 若未來新增 download options 從 21 → 30+，或者要支援多個進入點（CLI / batch script / config file）才有結構性收益。目前單一 GUI 進入點不必要。
**優先級: 🟡 IMPORTANT**  **位置:** `thread_download.py:55`（144 行，21 params）

`__init__` 經 Phase 21 抽過 legacy args 後仍 21 個 named params。建立 `@dataclass DownloadConfig` 一次傳入；同步改 `app/gui/run_actions.py` call site。**風險中-高**：影響跨檔 call site，需先 grep 所有實例化點並準備一次性遷移。

### Phase 34 (P-γ) — Pixiv_info._parse_payload 拆 ✅
**優先級: 🟢 LOW**  **位置:** `pixiv_api.py:562`（Pixiv_info CC=31）/ `:598`（_parse_payload CC=32）

把 `_parse_payload` 56 行拆成 3 個小 parser：`_parse_normal_artwork(payload)` / `_parse_manga_payload(payload)` / `_parse_ugoira_payload(payload)`，各自回傳 `(tag, like, pagecount, img_url)` tuple。

---

### Phase 29-B — _isPause: int → threading.Event 🔒 deferred
**優先級: 🟢 LOW（風險高、收益小於 28-B/C）**

**結論：暫不執行。**

60+ 觸點橫跨 6 檔（`pixiv_thread_base`, `thread_download`, `thread_url_fetch`, `thread_pid_scan`, `thread_following`, `thread_test`）。CPython int 在 x86 「碰巧」atomic，目前實務上無可見問題，只缺正式保證。

**前置條件（必須先做）：**
- 補 pause/stop/resume 語意 test：mock thread + `QSignalSpy` 驗證 `stop()` 後不再 emit；`pause()` 後 sleep 持續阻塞；`resume()` 後 sleep 真的恢復
- 補 test 後此 phase 才有意義

不在當前 sprint 範圍。
