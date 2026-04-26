# Progress Log

## Session 1 — 2026-04-25

### Started
- 讀取 pixiv_thread.py, pixiv_thread_utils.py, user_info.py, controller.py
- 確認所有重複位置（詳見 findings.md）
- 建立 task_plan.md, findings.md, progress.md

### In Progress
- Phase 1: Cookie 工具函數集中

### Todo
- Phase 2: PauseableThread base class

---

## Session 2 — 2026-04-25 (refactoring skill)

### Analysis
針對 `app/core/thread_download.py`（1946 行，最大且最常改動的檔案）做 code smell 掃描，識別出 5 個新重構項目（Phase 18-22），全數寫入 task_plan.md：

| # | 項目 | 位置 | 類型 |
|---|---|---|---|
| 18 | ugoira frame/GIF 方法 unify | 822-944 | Duplicate → Unify |
| 19 | 下載等待倒數方法 unify | 999-1057 | Duplicate → Unify |
| 20 | splitID guard clause 改寫 | 1261-1310 | Replace Nested Conditional |
| 21 | __init__ legacy args 抽離 | 122-178 | Extract Method |
| 22 | 下載檔名 helper 抽離 | 1792-1809 / 1888-1903 | Extract Method |

### Low-impact skipped
- 根目錄孤立重複檔 `pixiv_thread.py`, `pixiv_api.py`, `download_img.py`：刪除屬於行為變更（可能仍有 import 依賴），不納入重構範疇
- 100+ `except Exception: pass` 散佈各處：風格問題，非結構性 smell

### 建議下一步
Phase 18（ugoira unify）起手：純機械式、before/after 肉眼可驗證、立即刪除 60 行。Phase 19 接力同樣機械。Phase 20-22 依序。

---

## Session 3 — 2026-04-25 (py-complexity skill + 細項改寫)

### 重寫 Phase 18-22 細項
依 py-refactor / py-complexity / py-code-health / py-modernize 四個 skill 的工作流重寫每個 Phase：
- 每個 Phase 標註「技能對應」欄位
- 具體步驟細化為多個可單獨 commit 的 sub-step（例：Phase 20 拆成 test → extract → rewrite 三個 commit）
- 補上「驗證」欄位：`pytest` / `radon cc` / `vulture` 等具體命令
- Phase 21 從文字敘述改為 schema-driven（`_LEGACY_POSITIONAL` / `_LEGACY_KWARGS` 表格化）

### 新增 Phase 23-26（工具化守護）
- Phase 23 — py-code-health：vulture dead code + pylint duplicate 自動掃描
- Phase 24 — py-complexity：radon / lizard / xenon / wily baseline + 門檻守護；輸出 reports/
- Phase 25 — py-modernize：最小化 pyproject.toml（含 ruff/pytest 設定）、f-string sweep（用 ruff UP fix）；暫不升 Python 3.13，暫不做 pip→uv
- Phase 26 — py-refactor：最終 orchestrator 驗證，對比 Phase 24 baseline 確認不退步

### 專案 tooling 現況確認
- 無 `pyproject.toml`、無 `setup.py`，僅 `pytest.ini`
- 無 `from typing import List/Dict/Optional`（已是現代形式，無需改）
- 無 `datetime.utcnow()` 使用（無 deprecation 問題）
- `thread_download.py` 有 41 處 `.format(...)`（適合 UP-fix 批次現代化）
- 專案運行 Python 3.8（有 cpython-38 pyc），本輪 modernize 不升版本

---

## Session 4 — 2026-04-25 (執行 Phase 18-22)

### Baseline
- `pytest -q -m "not integration"`: **82 passed**

### Phase 18 ✅ — ugoira frame/GIF 方法 unify
- 發現 `_normalize_ugoira_frames_for_gif` + `_save_ugoira_gif`（path-based）**完全無呼叫者**，即 dead code
- 改為：刪除 dead pair、rename blob-based 為統一名稱（`_normalize_ugoira_frames`, `_save_ugoira_gif`）
- 比原計畫更簡單：不做 speculative unified iterator（Fowler "Don't Create Speculative Abstractions"）
- 120 行 → 58 行；82 tests pass

### Phase 19 ✅ — 下載等待倒數方法 unify
- Extract `_run_download_countdown(pid, min, max, *, label, color, respect_group_stop)`
- `_sleep_between_downloads` / `_sleep_within_pid` 改為 8 行的 delegating method
- 60 行 → 47 行；82 tests pass

### Phase 20 ✅ — splitID guard clause 改寫
- 先寫 12 個 golden-case 測試（`tests/test_split_id.py`）鎖行為
- 抽 3 個 static method parser：`_parse_pid_from_pid_equals`, `_parse_pid_from_pid_prefix`, `_parse_pid_from_underscore`
- 主 `splitID` 改為 guard + parser loop + `set()` 去重（取代 `np.unique`）
- 6 層 try/except 巢狀 → 2 層；94 tests pass（82 + 12 new）

### Phase 21 ✅ — __init__ legacy args 抽離
- Extract `_apply_legacy_constructor_args(legacy_args, legacy_kwargs)` static method
- Schema-driven：`positional_schema` / `scalar_schema` / list keys
- `__init__` legacy 段從 56 行降為 10 行
- 94 tests pass

### Phase 22 ✅ — 下載檔名 helper 抽離
- Extract `_build_download_filename(pid, *, page_suffix, ext, hashtag, timetag, notag, notime)` static method
- `gif_download` 與 `jpg_download` 檔名組裝區塊改為 10 行呼叫
- 新增 8 個 filename 組合單元測試（`tests/test_build_download_filename.py`）
- 102 tests pass（82 + 12 + 8）

### 總計
- 最終測試：**102 passed, 1 deselected**（+20 tests）
- `thread_download.py`：1946 → 1912 行（-34 行淨；但刪掉了 120+ 行重複 code + 添加 helpers 與 tests）
- 複雜度降幅最大在 `__init__`（legacy 段 -46 行）與 `splitID`（6 層 → 2 層）

### 暫停點
Phase 23-26 需安裝工具（vulture / pylint / radon / lizard / xenon / wily / ruff），均未安裝於目前 conda env。等待使用者同意安裝。

---

## Session 5 — 2026-04-25 (執行 Phase 23-26)

### 工具安裝
`pip install vulture pylint radon lizard xenon wily ruff` — 全部成功安裝至 `pixiv_env`（Python 3.10.19）

### Phase 23 ✅ — py-code-health 掃描
**Vulture findings（13 → 2 false positives）：**
- 移除 `pixiv_api.py` 中 4 個未使用 imports：`from logging import exception`、`from urllib import request`、`Options`（含 fallback 處）、`tqdm`（保留 `trange`）
- 移除 `pixiv_thread_base.py:19` 的 `import imageio,glob`（兩個都沒用到）
- 移除 `pixiv_api.py` 中 3 個 `raise` 語句（在 `break`/`return` 之後不可達）
- 移除 35 行 `'''docstring''' 形式的 commented-out code（lines 541-576）
- 移除 `thread_download.py:1047` 的 `return False`（在 try/except 都已 return 之後不可達）
- 建立 `vulture_whitelist.py` 鎖定 `icon_rc`（Qt resource side-effect import）和 `Pixiv_info(ip=None)` 公開 API 參數
- 最終 `vulture` exit=0（clean）

**Pylint duplicate findings：**
- 最大重複：`_write_all_url_file`（56 行，跨 thread_download / thread_url_fetch）
- 已記錄為未來 Phase P-δ（reports/split_plan.md）；不在 Phase 23 範圍

### Phase 24 ✅ — py-complexity baseline
**Reports 輸出至 `reports/`：**
- `complexity.txt`（radon cc）— top offenders：`get_download_url` F(52), `gif_download` E(37), `_convert_file_to_jxl` E(34), `Pixiv_info` E(31)
- `maintainability.txt`（radon mi）— `thread_download.py` C, `thread_url_fetch.py` C, `controller.py` C
- `cognitive.txt`（lizard）— 32 個函式超過 CC>15 或 length>100
- `split_plan.md`— 記錄未來 5 個拆分候選 phase（P-α 到 P-ε）

**wily build 失敗** — repo dirty。等首次 commit 後再 enable。

### Phase 25 ✅ — py-modernize
**pyproject.toml 建立：**
- `requires-python = ">=3.10"`
- `[tool.ruff]` target-version=py310, line-length=120, select=`["E","F","UP","B","SIM"]`, ignore=`["E501","B008","SIM117"]`
- `[tool.pytest.ini_options]` 含 integration marker
- 移除舊 `pytest.ini`（已被 pyproject.toml 取代）

**ruff --select UP --fix 套用 103 處：**
- 68× UP032: `.format(...)` → f-string
- 23× UP015: redundant-open-modes 移除
- 5× UP004: useless-object-inheritance（`class X(object)` → `class X`）
- 2× UP008: super-call-with-parameters
- 2× UP009: utf8-encoding-declaration（移除多餘的 `# -*- coding: utf-8 -*-`）
- 2× UP034: extraneous-parentheses

**ruff --select F401 --fix 套用 120 處：** 移除未使用 imports

**BOM 移除：** 8 個 `.py` 檔案有 UTF-8 BOM 導致 radon 無法解析；以 Python 腳本批次清除

**`pixiv_thread.py` 加 `__all__`：** 解決「shim 重新匯出被 ruff 誤判為 unused」問題

**ruff 違規數：469 → 335**（剩餘以 F405/F403 import-star 與 SIM105 try/except:pass 為大宗，皆為架構性問題）

### Phase 26 ✅ — 最終驗證
- `pytest -m "not integration"`: **102 passed**（無 regression）
- `vulture app/ vulture_whitelist.py --min-confidence 80`: **exit=0**（clean）
- 所有 11 個 `app.*` 模組 import 測試通過
- `CLAUDE.md` 已更新，記錄新加入的 ruff/radon/lizard/vulture 命令與 reports/ 路徑
- Reports 全套刷新存檔至 `reports/`

### 總計（Session 4 + 5）
- **測試：82 → 102 passed**（+20，無退步）
- **`thread_download.py`：1946 → 1876 行**（-70）
- **`pixiv_api.py`：939 → 890 行**（-49）
- **ruff 違規：469 → 335**（-134, -29%）
- **vulture：12 真死碼 + 2 false positives → 0 真死碼 + 2 whitelisted**
- **新增檔案：** `pyproject.toml`, `vulture_whitelist.py`, `tests/test_split_id.py`, `tests/test_build_download_filename.py`, `reports/{complexity,maintainability,cognitive,duplication,dead_code,split_plan,ruff_final}.txt|md`
- **移除檔案：** `pytest.ini`（合併進 pyproject.toml）

### 未做但有規劃（reports/split_plan.md）
- P-α: `thread_url_fetch.get_download_url` F(52) → D
- P-β: `gif_download` + `jpg_download` 拆 fetch helper
- P-γ: `Pixiv_info` 拆 `_parse_payload`
- P-δ: `_write_all_url_file` 上提到 utils（56 行重複）
- P-ε: 仍剩餘的 ruff 違規（F403/F405 import-star、SIM105）

---

## Session 6 — 2026-04-26 (code-review-skill → Phase 27)

### code-review 發現
- 跑 `Plan` agent 全面 review；發現 5 個 Blocking + 11 個 Important
- 最關鍵：`main.py` 從 repo 根啟動 → root `pixiv_api.py`(897 行) 影子化 `app/core/pixiv_api.py`(890 行)，Phase 25 的 vulture 清理、selenium try/except、`safe_read_json` 遷移在 runtime 全沒跑到
- 同問題影響 root `pixiv_thread.py` / `safe_io.py` / `pixiv_thread_utils.py`

### Phase 27 ✅ — 統一 import 路徑
**動作：**
1. 備份 root 6 個檔到 `backup/dead_root_dupes/`
2. 4 個被主流程載入的 root 檔轉成 1 行 shim：`pixiv_api.py` / `pixiv_thread.py` / `safe_io.py` / `pixiv_thread_utils.py` 全部改成 `from app.core.X import *` + 動態 `__all__`
3. 2 個非主流程孤立檔搬到 `backup/`：`download_img.py`(411 行)、`run_actions.py`(278 行) — 只被 `other/scripts/` 與 `.claude/worktrees/` 引用

**驗證：**
- `pytest -m "not integration"`: **102 passed**（與 Session 5 同）
- `python -c "import pixiv_api; print(pixiv_api.Pixiv_info.__module__)"` → `app.core.pixiv_api` ✅
- `safe_io.atomic_write_json.__module__` → `app.core.safe_io` ✅
- `pixiv_thread.download_thread.__module__` → `app.core.thread_download` ✅
- `from app.entry.main import main`: ok

**成果：** `app/core/pixiv_api.py`, `app/core/safe_io.py`, `app/core/pixiv_thread_utils.py`, `app/core/pixiv_thread.py` 變成真正被 runtime 載入的 source of truth；後續 Phase 28-29 修補才會生效。

### 待續
- Phase 28: 網路韌性（timeout / safe_json / verify=True / 退避）
- Phase 29: Thread lifecycle + `pass.json` 拆分
