# 依作者順序下載 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Step 4 下載時可選「依作者順序」— 同一作者的作品連續下載完(PID 由大到小)再換下一位作者,作者之間維持發現順序,作者不明者排最後,pool 模式嚴格一作者一作者。

**Architecture:** 採記憶體內重排(spec 方案 A)。在 `_group_urls_by_pid` 算出 `pid_order` 後、執行下載前,用一個純函式 `compute_author_order` 重排出 `(flat_order, author_batches)`;單執行緒循序消費 `flat_order` 即嚴格,多執行緒 `_execute_downloads_pool` 改成逐作者 batch + barrier。零 schema / SQL / view 變更,以 `download.author_order` 開關 opt-in,關閉時行為與今日完全相同。

**Tech Stack:** Python 3.13、SQLite(`app/core/metadata_db.py`)、`threading` + `concurrent.futures`、Flet(`app/gui/views/settings_view.py`)、pytest。

參考 spec:`docs/superpowers/specs/2026-06-04-author-order-download-design.md`

---

## File Structure

| 檔案 | 動作 | 職責 |
|---|---|---|
| `app/core/metadata_db.py` | 修改 | 新增 `user_id_map_for_pids(pids)` 批次查詢(`get_artwork` 旁) |
| `app/core/thread_download.py` | 修改 | 新增 module-level 純函式 `compute_author_order`;instance `_reorder_pid_order_by_author` + `_resolve_execution_order`;改 `run()`、`_execute_downloads`、`_execute_downloads_pool`;`author_order` 接線 |
| `app/core/settings_store.py` | 修改 | `DEFAULTS["download"]` 加 `author_order: False` |
| `app/gui/views/settings_view.py` | 修改 | 新增 `ft.Switch` + layout + `to_dict` |
| `app/gui/run_actions.py` | 修改 | `_build_step4` 把 `author_order` 傳給 `download_thread(...)` |
| `tests/test_user_id_map_for_pids.py` | 新增 | DB 批次查詢測試 |
| `tests/test_compute_author_order.py` | 新增 | 純函式排序測試 |
| `tests/test_reorder_pid_order_by_author.py` | 新增 | glue + `_resolve_execution_order` 測試 |
| `tests/test_execute_downloads_pool_author_barrier.py` | 新增 | pool barrier 行為測試 + off-path 回歸 |
| `tests/test_author_order_wiring.py` | 新增 | 常數 plumbing + settings 預設 |

---

## Task 1: `user_id_map_for_pids` 批次查詢(metadata_db)

**Files:**
- Modify: `app/core/metadata_db.py`(置於 `get_artwork` 之後,約 `:946`)
- Test: `tests/test_user_id_map_for_pids.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for MetadataDB.user_id_map_for_pids — bulk pid -> user_id lookup."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB


def _open(tmp_path):
    return MetadataDB(str(tmp_path), event_log=None)


def test_returns_user_id_for_known_pids(tmp_path):
    db = _open(tmp_path)
    db.upsert_artwork("100", user_id="111")
    db.upsert_artwork("200", user_id="222")
    out = db.user_id_map_for_pids(["100", "200"])
    assert out == {"100": "111", "200": "222"}


def test_missing_pid_maps_to_none(tmp_path):
    db = _open(tmp_path)
    db.upsert_artwork("100", user_id="111")
    out = db.user_id_map_for_pids(["100", "999"])
    assert out["100"] == "111"
    assert out["999"] is None


def test_null_or_empty_user_id_maps_to_none(tmp_path):
    db = _open(tmp_path)
    db.upsert_artwork("100")              # no user_id -> NULL
    db.upsert_artwork("200", user_id="")  # empty string
    out = db.user_id_map_for_pids(["100", "200"])
    assert out["100"] is None
    assert out["200"] is None


def test_keys_are_original_input_pids(tmp_path):
    db = _open(tmp_path)
    db.upsert_artwork("100", user_id="111")
    # page-suffixed input coerces to "100" for the query but the returned
    # key must be the exact value passed in.
    out = db.user_id_map_for_pids(["100_p3"])
    assert out == {"100_p3": "111"}


def test_chunking_over_900_pids(tmp_path):
    db = _open(tmp_path)
    pids = [str(i) for i in range(1, 1001)]      # 1000 pids
    for p in pids:
        db.upsert_artwork(p, user_id="u" + p)
    out = db.user_id_map_for_pids(pids)
    assert len(out) == 1000
    assert out["1"] == "u1"
    assert out["1000"] == "u1000"


def test_empty_input_returns_empty(tmp_path):
    db = _open(tmp_path)
    assert db.user_id_map_for_pids([]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_user_id_map_for_pids.py -v`
Expected: FAIL — `AttributeError: 'MetadataDB' object has no attribute 'user_id_map_for_pids'`

- [ ] **Step 3: Write minimal implementation**

在 `app/core/metadata_db.py` 的 `get_artwork` 方法之後(約 `:946` 之後)新增:

```python
    def user_id_map_for_pids(self, pids: Iterable[str]) -> dict[str, str | None]:
        """Return ``{original_pid: user_id|None}`` for the given pids.

        Keys are the exact pid strings passed in (not the coerced digit
        form), so callers can look up by the same values they hold. A pid
        absent from ``artworks``, or whose ``user_id`` is NULL/empty, maps
        to ``None``. Coerced pids are batched in chunks of 900 to stay under
        SQLite's bound-variable limit.
        """
        out: dict[str, str | None] = {}
        coerced_to_orig: dict[str, list[str]] = {}
        for p in pids:
            out[p] = None
            c = self._coerce_pid(p)
            if c:
                coerced_to_orig.setdefault(c, []).append(p)
        if not coerced_to_orig:
            return out
        conn = self._conn()
        keys = list(coerced_to_orig.keys())
        chunk = 900
        for i in range(0, len(keys), chunk):
            part = keys[i:i + chunk]
            placeholders = ",".join("?" * len(part))
            cur = conn.execute(
                f"SELECT pid, user_id FROM artworks WHERE pid IN ({placeholders})",
                part,
            )
            for cpid, uid in cur.fetchall():
                val = uid if (uid is not None and str(uid).strip() != "") else None
                for orig in coerced_to_orig.get(str(cpid), []):
                    out[orig] = val
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_user_id_map_for_pids.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_user_id_map_for_pids.py app/core/metadata_db.py
git commit -m "feat(metadata_db): user_id_map_for_pids bulk lookup"
```

---

## Task 2: `compute_author_order` 純函式(thread_download)

**Files:**
- Modify: `app/core/thread_download.py`(module scope,置於現有 module-level 常數附近,例如 `_DECORATIVE_CHARS_RE` 之後)
- Test: `tests/test_compute_author_order.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for compute_author_order — pure author-grouping reorder."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import compute_author_order


def test_groups_by_author_preserving_first_encounter_order():
    # author B appears first, then A. Output keeps B-before-A.
    pid_order = ["10", "20", "30"]      # 10->B, 20->A, 30->B
    uid = {"10": "B", "20": "A", "30": "B"}
    flat, batches = compute_author_order(pid_order, uid)
    # B's batch first (30,10 desc), then A's (20)
    assert batches == [["30", "10"], ["20"]]
    assert flat == ["30", "10", "20"]


def test_within_author_pid_descending():
    pid_order = ["5", "100", "30"]
    uid = {"5": "A", "100": "A", "30": "A"}
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["100", "30", "5"]]
    assert flat == ["100", "30", "5"]


def test_unknown_authors_bucketed_last():
    pid_order = ["10", "20", "30"]      # 20 unknown
    uid = {"10": "A", "20": None, "30": "A"}
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["30", "10"], ["20"]]   # unknown bucket last
    assert flat == ["30", "10", "20"]


def test_empty_string_user_id_is_unknown():
    pid_order = ["10", "20"]
    uid = {"10": "", "20": "A"}
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["20"], ["10"]]


def test_missing_pid_in_map_is_unknown():
    pid_order = ["10", "20"]
    uid = {"10": "A"}                   # 20 absent
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["10"], ["20"]]


def test_non_numeric_pid_falls_back_to_reverse_lexical_after_digits():
    pid_order = ["abc", "100", "9"]
    uid = {"abc": "A", "100": "A", "9": "A"}
    flat, batches = compute_author_order(pid_order, uid)
    # digits descending first (100, 9), then non-digits reverse-lexical (abc)
    assert batches == [["100", "9", "abc"]]


def test_empty_input():
    flat, batches = compute_author_order([], {})
    assert flat == []
    assert batches == []


def test_only_unknown_authors():
    pid_order = ["30", "10", "20"]
    uid = {}
    flat, batches = compute_author_order(pid_order, uid)
    assert batches == [["30", "20", "10"]]   # single unknown bucket, PID desc
    assert flat == ["30", "20", "10"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compute_author_order.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_author_order'`

- [ ] **Step 3: Write minimal implementation**

在 `app/core/thread_download.py` module scope(現有 module-level 常數附近)新增:

```python
def _within_author_sorted(pids: list[str]) -> list[str]:
    """Sort one author's pids: numeric PIDs descending, then any
    non-numeric pids in reverse-lexical order at the end (deterministic)."""
    digits = sorted((p for p in pids if str(p).isdigit()),
                    key=lambda p: int(p), reverse=True)
    nondigits = sorted((p for p in pids if not str(p).isdigit()), reverse=True)
    return digits + nondigits


def compute_author_order(pid_order, pid_to_user_id):
    """Reorder pids so each author's works are contiguous.

    - Authors are sequenced by first-encounter order in ``pid_order``.
    - Within an author, pids are PID-descending (see _within_author_sorted).
    - pids whose user_id is None/empty/missing form one "unknown" bucket
      appended last.

    Returns ``(flat_order, author_batches)`` where ``author_batches`` is a
    list of per-author pid lists (one batch per author, unknown bucket last)
    and ``flat_order`` is those batches concatenated.
    """
    author_seq: list[str] = []
    groups: dict[str, list[str]] = {}
    unknown: list[str] = []
    for pid in pid_order:
        uid = pid_to_user_id.get(pid)
        key = "" if uid is None else str(uid).strip()
        if not key:
            unknown.append(pid)
            continue
        if key not in groups:
            groups[key] = []
            author_seq.append(key)
        groups[key].append(pid)
    author_batches = [_within_author_sorted(groups[k]) for k in author_seq]
    if unknown:
        author_batches.append(_within_author_sorted(unknown))
    flat_order = [pid for batch in author_batches for pid in batch]
    return flat_order, author_batches
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_compute_author_order.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_compute_author_order.py app/core/thread_download.py
git commit -m "feat(thread_download): compute_author_order pure reorder helper"
```

---

## Task 3: glue 方法 `_reorder_pid_order_by_author` + `_resolve_execution_order`

**Files:**
- Modify: `app/core/thread_download.py`(instance 方法,置於 `_group_urls_by_pid` `:1786` 附近)
- Test: `tests/test_reorder_pid_order_by_author.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for download_thread._reorder_pid_order_by_author and
_resolve_execution_order. We use __new__ to skip the heavy __init__ and
stub only the attributes these methods touch."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


class _FakeQ:
    def __init__(self):
        self.events = []

    def put(self, ev):
        self.events.append(ev)


class _FakeDB:
    def __init__(self, mapping):
        self._m = mapping

    def user_id_map_for_pids(self, pids):
        return {p: self._m.get(p) for p in pids}


def _make(mapping):
    t = download_thread.__new__(download_thread)
    t._q = _FakeQ()
    t._metadata_db = _FakeDB(mapping)
    t._emit_phase = lambda *a, **k: None
    return t


def test_reorder_groups_by_author_and_buckets_unknown_last():
    t = _make({"10": "A", "20": "B", "30": "A"})
    flat, batches = t._reorder_pid_order_by_author(["10", "20", "30"])
    assert batches == [["30", "10"], ["20"]]
    assert flat == ["30", "10", "20"]


def test_reorder_emits_unknown_count_warning():
    t = _make({"10": "A", "20": None})
    t._reorder_pid_order_by_author(["10", "20"])
    texts = [str(getattr(ev, "data", "")) for ev in t._q.events]
    assert any("作者不明" in x for x in texts)


def test_reorder_no_warning_when_all_known():
    t = _make({"10": "A", "20": "A"})
    t._reorder_pid_order_by_author(["10", "20"])
    texts = [str(getattr(ev, "data", "")) for ev in t._q.events]
    assert not any("作者不明" in x for x in texts)


def test_reorder_falls_back_to_single_batch_without_db():
    t = download_thread.__new__(download_thread)
    t._metadata_db = None
    flat, batches = t._reorder_pid_order_by_author(["10", "20"])
    assert flat == ["10", "20"]
    assert batches == [["10", "20"]]


def test_resolve_execution_order_off_returns_unchanged_single_batch():
    t = download_thread.__new__(download_thread)
    t.author_order = False
    flat, batches = t._resolve_execution_order(["10", "20", "30"])
    assert flat == ["10", "20", "30"]
    assert batches == [["10", "20", "30"]]


def test_resolve_execution_order_on_delegates_to_reorder():
    t = _make({"10": "A", "20": "B"})
    t.author_order = True
    flat, batches = t._resolve_execution_order(["10", "20"])
    assert batches == [["10"], ["20"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reorder_pid_order_by_author.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_reorder_pid_order_by_author'`

- [ ] **Step 3: Write minimal implementation**

在 `app/core/thread_download.py` 的 `_group_urls_by_pid`(`:1786-1797`)之後新增兩個 instance 方法:

```python
    def _reorder_pid_order_by_author(self, pid_order):
        """Reorder pid_order so same-author works are contiguous.

        Returns ``(flat_order, author_batches)``. Falls back to a single
        batch (current behavior) when no metadata DB is available."""
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return list(pid_order), [list(pid_order)]
        try:
            uid_map = db.user_id_map_for_pids(pid_order)
        except Exception:
            return list(pid_order), [list(pid_order)]
        flat_order, author_batches = compute_author_order(pid_order, uid_map)
        unknown_count = sum(
            1 for p in pid_order if not str(uid_map.get(p) or "").strip()
        )
        if unknown_count:
            self._q.put(WorkerEvent("output",
                f"<p><font color='orange'>[作者排序] {unknown_count} 筆作品作者不明，"
                f"已排到最後；重跑步驟 2/3 可補作者資料</font></p>"))
        return flat_order, author_batches

    def _resolve_execution_order(self, pid_order):
        """Decide final PID sequence + per-author batches for the executors.

        author_order off -> (pid_order unchanged, single batch) [zero regression]
        author_order on  -> author-grouped reorder
        """
        if getattr(self, "author_order", False):
            return self._reorder_pid_order_by_author(pid_order)
        return list(pid_order), [list(pid_order)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reorder_pid_order_by_author.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_reorder_pid_order_by_author.py app/core/thread_download.py
git commit -m "feat(thread_download): author-order reorder glue + execution-order resolver"
```

---

## Task 4: pool barrier + `run()` 接線 + `_execute_downloads` 簽名

**Files:**
- Modify: `app/core/thread_download.py`
  - `_execute_downloads`(`:2044-2047`)
  - `_execute_downloads_pool`(`:2021-2042`)
  - `run()`(`:2353-2363`)
- Test: `tests/test_execute_downloads_pool_author_barrier.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for _execute_downloads_pool's per-author batching/barrier and the
off-path single-batch regression. Uses __new__ + stubs."""
from pathlib import Path
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


class _FakeQ:
    def put(self, ev):
        pass


def _make_pool_thread(record):
    t = download_thread.__new__(download_thread)
    t._q = _FakeQ()
    t._stop_event = threading.Event()
    t._step4_pid_total = 0
    t._step4_pid_done = 0
    t._emit_phase = lambda *a, **k: None
    t._maybe_flush_url_meta_periodically = lambda done: None
    lock = threading.Lock()

    def _dl(pid, urls):
        with lock:
            record.append(pid)
        return []

    t._download_pid_group = _dl
    return t


def test_pool_processes_each_author_batch_before_the_next():
    record = []
    t = _make_pool_thread(record)
    batch_a = ["30", "10"]
    batch_b = ["99"]
    groups = {"30": ["u"], "10": ["u"], "99": ["u"]}
    t._step4_pid_total = 3
    t._execute_downloads_pool([batch_a, batch_b], groups)
    # All of batch A must be recorded before any of batch B — the next
    # batch isn't submitted until the current batch's futures all drain.
    assert set(record[:2]) == {"30", "10"}
    assert record[2] == "99"


def test_pool_single_batch_processes_all_pids_offpath():
    record = []
    t = _make_pool_thread(record)
    pid_order = ["30", "10", "20"]
    groups = {p: ["u"] for p in pid_order}
    t._step4_pid_total = 3
    failed = t._execute_downloads_pool([pid_order], groups)
    assert set(record) == {"30", "10", "20"}
    assert len(failed) == 3


def test_pool_stops_between_batches_when_stop_set():
    record = []
    t = _make_pool_thread(record)
    t._stop_event.set()
    t._execute_downloads_pool([["1"], ["2"]], {"1": ["u"], "2": ["u"]})
    assert record == []   # stop checked before submitting the first batch
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_execute_downloads_pool_author_barrier.py -v`
Expected: FAIL — `_execute_downloads_pool` 目前簽名是 `(pid_order, pid_groups)` 且無 batch 迴圈,測試傳 batches 會斷言失敗或 `TypeError`。

- [ ] **Step 3: Write minimal implementation**

(a) 替換 `_execute_downloads_pool`(`:2021-2042`)為逐 batch + barrier 版本:

```python
    def _execute_downloads_pool(self, author_batches, pid_groups):
        """Multi-thread per-PID-group download via ThreadPoolExecutor.

        Processes ``author_batches`` one batch (= one author) at a time:
        submit all of a batch's PIDs, drain them (as_completed), then move to
        the next batch. With a single batch this is identical to the previous
        submit-all behavior. Author-order strictness is the per-batch barrier.
        """
        self._q.put(WorkerEvent("output",
            "<p><font color='gray'>下載模式：多執行緒（以 PID 為單位分派；每個 PID 仍共用單一 Session）</font></p>"))
        failed_nested = []
        total = getattr(self, "_step4_pid_total",
                        sum(len(b) for b in author_batches))
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as self.executor:
            for batch in author_batches:
                if self._stop_event.is_set():
                    break
                futures = [
                    self.executor.submit(self._download_pid_group, pid, pid_groups.get(pid, []))
                    for pid in batch
                ]
                for fu in concurrent.futures.as_completed(futures):
                    try:
                        failed_nested.append(fu.result())
                    except Exception:
                        failed_nested.append([])
                    done += 1
                    self._step4_pid_done = done
                    self._emit_phase(f"步驟 4：下載中（{done} / {total} PID 完成）")
                    self._maybe_flush_url_meta_periodically(done)
        return failed_nested
```

(b) 改 `_execute_downloads`(`:2044-2047`)加第三參數並轉傳:

```python
    def _execute_downloads(self, pid_order, pid_groups, author_batches):
        if self.single_mode_flag:
            return self._execute_downloads_single(pid_order, pid_groups)
        return self._execute_downloads_pool(author_batches, pid_groups)
```

(c) 改 `run()`:把 `_group_urls_by_pid` 之後到 `_execute_downloads` 之間(`:2356-2363`)改成:

```python
            pid_order, pid_groups = self._group_urls_by_pid(self.allurl)
            if getattr(self, "author_order", False):
                self._emit_phase("步驟 4：依作者排序中...")
            pid_order, author_batches = self._resolve_execution_order(pid_order)
            self._diag("step4_grouped", pid_count=len(pid_order), url_count=len(self.allurl))
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[Step4] PID 分組完成：{len(pid_order)} 個 PID、{len(self.allurl)} 個 URL</font></p>"))
            self._emit_phase(f"步驟 4：下載中（0 / {len(pid_order)} PID 完成）")
            self._step4_pid_total = len(pid_order)
            self._step4_pid_done = 0
            failed_nested = self._execute_downloads(pid_order, pid_groups, author_batches)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_execute_downloads_pool_author_barrier.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run existing Step 4 helper tests for regression**

Run: `pytest tests/test_step4_download_helpers.py -v`
Expected: PASS。若有測試直接以兩參數呼叫 `_execute_downloads(pid_order, pid_groups)`,在本步驟同步改為三參數 `_execute_downloads(pid_order, pid_groups, [pid_order])`。

- [ ] **Step 6: Commit**

```bash
git add tests/test_execute_downloads_pool_author_barrier.py app/core/thread_download.py
git commit -m "feat(thread_download): per-author barrier in pool mode + wire run()"
```

---

## Task 5: 設定接線(開關 + 預設 + UI + run_actions)

**Files:**
- Modify: `app/core/thread_download.py`(`_LEGACY_SCALAR_KW_SCHEMA` `:916-926`;`__init__` `:159-176`)
- Modify: `app/core/settings_store.py`(`DEFAULTS["download"]` `:15-35`)
- Modify: `app/gui/views/settings_view.py`(switch 定義 `:77-81`;layout `:495-507`;`to_dict` `:378-389`)
- Modify: `app/gui/run_actions.py`(`_build_step4` `:637`)
- Test: `tests/test_author_order_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
"""Wiring tests: author_order kwarg plumbing + settings default."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread
from app.core.settings_store import DEFAULTS


def test_author_order_in_legacy_scalar_schema():
    keys = [k for k, _ in download_thread._LEGACY_SCALAR_KW_SCHEMA]
    assert "author_order" in keys


def test_author_order_kwarg_plumbs_into_overrides():
    overrides = {}
    download_thread._apply_legacy_scalar_kwargs({"author_order": True}, overrides)
    assert overrides["author_order"] is True


def test_author_order_default_false():
    assert DEFAULTS["download"]["author_order"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_author_order_wiring.py -v`
Expected: FAIL — `author_order` 不在 schema、`DEFAULTS["download"]` 無此鍵。

- [ ] **Step 3a: Add to `_LEGACY_SCALAR_KW_SCHEMA`**

`app/core/thread_download.py:925`(`("tag_strip_special_chars", bool),` 那行)之後加一行:

```python
        ("author_order", bool),
```

- [ ] **Step 3b: Store the attribute in `__init__`**

`app/core/thread_download.py`,在 `self.ai_gen_dir = overrides.get("ai_gen_dir", self.ai_gen_dir)`(`:160`)之後加一行:

```python
        self.author_order = bool(overrides.get("author_order", False))
```

- [ ] **Step 3c: Add the settings default**

`app/core/settings_store.py`,在 `DEFAULTS["download"]` 的 `"tag_strip_special_chars": False,`(`:30`)之後加:

```python
        # When true, Step 4 downloads one author's works fully (PID desc)
        # before moving to the next author; unknown-author works go last.
        "author_order": False,
```

- [ ] **Step 3d: Add the Settings UI switch**

`app/gui/views/settings_view.py`,在 `self._sw_tag_strip_special_chars = ft.Switch(...)`(`:77-81`)之後加:

```python
        self._sw_author_order = ft.Switch(
            label="依作者順序下載（同作者連續）",
            value=bool(dl.get("author_order", False)),
            tooltip="開啟後，步驟 4 會把同一作者的作品連續下載完（PID 由大到小）再換下一位；作者不明的作品排到最後",
        )
```

- [ ] **Step 3e: Place the switch in the layout**

`app/gui/views/settings_view.py`,在「檔名範本」tile 的 `ft.Row([self._sw_tag_strip_brackets, self._sw_tag_strip_special_chars], wrap=True)`(`:503-506`)之後、該 `_tile(...)` 的結尾 `])` 之前,加入:

```python
                    ft.Text("下載順序", size=12),
                    self._sw_author_order,
```

- [ ] **Step 3f: Persist in `to_dict`**

`app/gui/views/settings_view.py`,在 `store.update_section("download", { ... })` 的 `"tag_strip_special_chars": bool(self._sw_tag_strip_special_chars.value),`(`:388`)之後加:

```python
            "author_order": bool(self._sw_author_order.value),
```

- [ ] **Step 3g: Pass it through `_build_step4`**

`app/gui/run_actions.py`,在 `download_thread(...)` 呼叫中 `tag_strip_special_chars=bool(dl.get("tag_strip_special_chars", False)),`(`:638`)之後加:

```python
            author_order=bool(dl.get("author_order", False)),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_author_order_wiring.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Verify wiring points by grep**

Run:
```bash
grep -rn "author_order" app/core/thread_download.py app/core/settings_store.py app/gui/views/settings_view.py app/gui/run_actions.py
```
Expected: `thread_download.py`(schema + `__init__` store + ordering 方法引用)、`settings_store.py`(1 default)、`settings_view.py`(switch 定義 + layout + to_dict 共 3 處)、`run_actions.py`(1 處 `_build_step4`)都出現。

- [ ] **Step 6: Commit**

```bash
git add tests/test_author_order_wiring.py app/core/thread_download.py app/core/settings_store.py app/gui/views/settings_view.py app/gui/run_actions.py
git commit -m "feat: wire download.author_order setting through Step 4"
```

---

## Task 6: 全套件回歸 + 手動冒煙

**Files:** none(驗證)

- [ ] **Step 1: Run the full unit suite**

Run: `pytest -m 'not integration'`
Expected: 全綠(既有測試 + 本計畫新增 ~26 個測試)。任何 RED 先回對應 Task 修正,不要往下走。

- [ ] **Step 2: Lint the touched files**

Run: `ruff check app/core/thread_download.py app/core/metadata_db.py app/core/settings_store.py app/gui/views/settings_view.py app/gui/run_actions.py`
Expected: 無新違規(E/F/UP/B/SIM)。

- [ ] **Step 3: Manual smoke (desktop)**

Run: `python main.py`
- 設定頁出現「依作者順序下載（同作者連續）」開關;切換並儲存後重開 App 該值有保留(round-trip)。
- 開啟開關跑一次步驟 4:log 出現「依作者排序中...」與(若有舊資料)「N 筆作品作者不明，已排到最後」;下載順序依作者連續、同作者內新圖(大 PID)先。
- 關閉開關跑一次:行為與改動前一致(無排序 log)。

- [ ] **Step 4: Final commit (if any smoke fixups)**

```bash
git add -A
git commit -m "test: author-order download — full suite green + smoke verified"
```

---

## Self-Review

**1. Spec coverage(逐條對照 spec)**
- 作者之間維持發現順序 → Task 2 `compute_author_order`(`author_seq` 首次出現)+ test `test_groups_by_author_preserving_first_encounter_order`. ✓
- 作者內部 PID 由大到小 → Task 2 `_within_author_sorted` + test `test_within_author_pid_descending`. ✓
- 作者不明排最後 → Task 2 unknown bucket + tests `test_unknown_authors_bucketed_last` / `test_empty_string_user_id_is_unknown` / `test_missing_pid_in_map_is_unknown`. ✓
- 嚴格一作者一作者(pool) → Task 4 per-batch barrier + test `test_pool_processes_each_author_batch_before_the_next`;單執行緒循序天然滿足(吃 `flat_order`). ✓
- DB 批次查 user_id → Task 1 `user_id_map_for_pids`(含 chunk、None、原始 pid 鍵). ✓
- 開關 opt-in、預設關、零回歸 → Task 5 wiring + Task 4 `test_pool_single_batch_processes_all_pids_offpath` + Task 3 `test_resolve_execution_order_off_returns_unchanged_single_batch`. ✓
- 舊資料 NULL 提示 log → Task 3 `test_reorder_emits_unknown_count_warning`. ✓
- 涵蓋 fallback / 失敗重排路徑 → 重排作用於 `run()` 內 `_group_urls_by_pid` 之後的最終 `pid_order`,失敗 URL 已於建構期併入 `self.allurl`(spec 動機段),故一併被排序。✓
- 繁體中文使用者字串 → switch label/tooltip、log 皆繁中。✓

**2. Placeholder scan:** 無 TBD/TODO;每個改碼步驟都有完整 code block 與確切檔案/行號。✓

**3. Type consistency:**
- `compute_author_order(pid_order, pid_to_user_id) -> (flat_order, author_batches)` 在 Task 2 定義,Task 3 glue 與測試都用相同回傳形狀。✓
- `_execute_downloads(pid_order, pid_groups, author_batches)` 三參數在 Task 4 定義並由 `run()` 同步呼叫;`_execute_downloads_pool(author_batches, pid_groups)` 與轉傳一致;測試用相同簽名。✓
- `user_id_map_for_pids` 回傳 `dict[str, str|None]`,Task 3 `_FakeDB` 與真實 glue 以相同形狀消費。✓
- 設定鍵字串 `"author_order"` 在 schema / default / settings_view / run_actions / `__init__` 五處字面一致。✓
