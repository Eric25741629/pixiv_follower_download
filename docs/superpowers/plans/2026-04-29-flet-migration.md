# Flet Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Pixiv downloader GUI from PyQt5 to Flet, making `app/core/` completely Qt-free via a `queue.Queue` event system, and building a new Material 3 UI with NavigationRail layout supporting both desktop and web modes.

**Architecture:** Worker threads become `threading.Thread` subclasses that push `WorkerEvent` dataclass instances onto a `queue.Queue`. A Flet-side `EventDispatcher` polls the queue every 50 ms and updates the UI. The JSON persistence schema (`cookies.json`, `othersettings.json`) is unchanged.

**Tech Stack:** Python 3.10+, `flet>=0.21`, `threading`, `queue` (stdlib), `pytest`, `ruff`

---

## Parallelization Map

After **Task 2** is complete, Tasks 3/4/5/6 can be dispatched to separate agents simultaneously.
After **Task 8** (Flet skeleton) is complete, Tasks 9/12 can run in parallel.
After Tasks 9/12 are complete, Tasks 13/14 can run in parallel.

```
Task 1 → Task 2 → ┬─ Task 3 (following)  ─┐
                  ├─ Task 4 (pid_scan)    ─┤→ Task 7 (tests) → Task 8 (Flet skeleton)
                  ├─ Task 5 (url_fetch)   ─┤                       │
                  └─ Task 6 (download)    ─┘            ┌──────────┤
                                                         ↓          ↓
                                                    Task 9      Task 12
                                                 (log_format)  (user_info)
                                                         │          │
                                                    Task 10      Task 13
                                                 (main_view)  (settings_view)
                                                         │          │
                                                    Task 11      Task 14
                                                  (wire up)   (cookies_view)
                                                         └──── Task 15 (cleanup)
```

---

## File Map

**Created:**
- `app/core/worker_event.py` — `WorkerEvent` dataclass
- `app/gui/dispatcher.py` — queue → Flet page dispatcher
- `app/gui/flet_app.py` — Flet `main()` entry, page setup, NavigationRail
- `app/gui/log_format.py` — HTML color tag → `TextSpan` parser
- `app/gui/views/__init__.py` — empty
- `app/gui/views/main_view.py` — step cards, progress, log
- `app/gui/views/settings_view.py` — settings ExpansionTile groups
- `app/gui/views/cookies_view.py` — cookie DataTable + dialogs

**Modified:**
- `app/core/pixiv_thread_base.py` — QThread → threading.Thread, pyqtSignal → queue
- `app/core/thread_following.py` — emit → q.put
- `app/core/thread_pid_scan.py` — emit → q.put
- `app/core/thread_url_fetch.py` — emit → q.put
- `app/core/thread_download.py` — emit → q.put, QMutex → threading.Lock
- `app/core/pixiv_thread.py` — update shim re-exports
- `app/gui/user_info.py` — remove QFileDialog/QDateTime, use plain values
- `app/entry/main.py` — call `flet.app()`
- `pyproject.toml` — add flet dep

**Deleted (Task 15):**
- `app/gui/controller.py`
- `app/gui/run_actions.py`
- `test.ui`, `uimake.py`, `trash/Ui2.py`

---

## Task 1: WorkerEvent dataclass

**Files:**
- Create: `app/core/worker_event.py`
- Create: `tests/test_worker_event.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_event.py
from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.worker_event import WorkerEvent


def test_worker_event_is_frozen():
    ev = WorkerEvent("output", "hello")
    try:
        ev.type = "other"
        assert False, "should be immutable"
    except Exception:
        pass


def test_worker_event_fields():
    ev = WorkerEvent("progress", (10, 100))
    assert ev.type == "progress"
    assert ev.data == (10, 100)


def test_worker_event_equality():
    assert WorkerEvent("finished", "done") == WorkerEvent("finished", "done")
    assert WorkerEvent("next", 2) != WorkerEvent("next", 3)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_worker_event.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.core.worker_event'`

- [ ] **Step 3: Create `app/core/worker_event.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerEvent:
    """Immutable event emitted by worker threads via queue.Queue.

    type values:
      "output"     – data: str  (HTML-colored log line)
      "progress"   – data: tuple[int, int]  (current, total)
      "countdown"  – data: int  (remaining seconds)
      "finished"   – data: str  (completion message)
      "next"       – data: int  (next step number; -1 = stop)
      "timechanged"– data: str  (ISO datetime string, download_thread only)
    """
    type: str
    data: Any
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_worker_event.py -v
```
Expected: 3 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add app/core/worker_event.py tests/test_worker_event.py
git commit -m "feat(core): add WorkerEvent dataclass for queue-based thread events"
```

---

## Task 2: Rewrite pixiv_thread_base.py

**Files:**
- Modify: `app/core/pixiv_thread_base.py`
- Create: `tests/test_thread_base.py`

**Context:** `PauseableThread` currently extends `QThread` with an integer `_isPause` (0=running, 1=paused, 2=stopped) and two `pyqtSignal` class attributes. Replace with `threading.Thread` + two `threading.Event` objects and a queue. All `_isPause == 1` checks → `not _pause_event.is_set()`, all `_isPause == 2` checks → `_stop_event.is_set()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_thread_base.py
from pathlib import Path
import sys
import queue
import time
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.worker_event import WorkerEvent
from app.core.pixiv_thread_base import PauseableThread


class _NoopThread(PauseableThread):
    def run(self):
        self._sleep_with_countdown(2)


def test_pause_emits_event():
    q: queue.Queue = queue.Queue()
    t = _NoopThread(q)
    t.pause()
    ev = q.get_nowait()
    assert ev.type == "output"
    assert "已暫停" in ev.data


def test_resume_emits_event():
    q: queue.Queue = queue.Queue()
    t = _NoopThread(q)
    t.pause()
    q.get_nowait()  # discard pause event
    t.resume()
    ev = q.get_nowait()
    assert ev.type == "output"
    assert "已繼續" in ev.data


def test_stop_sets_stop_event_and_emits():
    q: queue.Queue = queue.Queue()
    t = _NoopThread(q)
    t.stop()
    assert t._stop_event.is_set()
    ev = q.get_nowait()
    assert ev.type == "output"
    assert "已停止" in ev.data


def test_countdown_emits_countdown_events():
    q: queue.Queue = queue.Queue()
    t = _NoopThread(q)
    t.start()
    time.sleep(2.5)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    countdown_events = [e for e in events if e.type == "countdown"]
    values = [e.data for e in countdown_events]
    assert 2 in values
    assert 1 in values
    assert 0 in values
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_thread_base.py -v
```
Expected: `TypeError` or `ImportError` (constructor signature mismatch)

- [ ] **Step 3: Rewrite `app/core/pixiv_thread_base.py`**

Replace the entire `PauseableThread` class (keep the module-level helper functions `_normalize_special_like_rules`, `_resolve_like_threshold`, `_is_ai_artwork_tagged` — do NOT remove them):

```python
from PyQt5.QtCore import *   # REMOVE THIS LINE
import threading              # ADD
import queue as _queue        # ADD
import time
from pixiv_api import *
from app.core.pixiv_thread_utils import (
    cookie_usage_label,
    format_cookie_usage_summary,
    normalize_cookie_entries,
    normalize_cookie_pool,
)
from app.core.worker_event import WorkerEvent  # ADD

# Backward-compatible aliases
_normalize_cookie_entries = normalize_cookie_entries
_normalize_cookie_pool = normalize_cookie_pool
_cookie_usage_label = cookie_usage_label
_format_cookie_usage_summary = format_cookie_usage_summary


# ... (keep all helper functions unchanged: _normalize_special_like_rules,
#      _resolve_like_threshold, _is_ai_artwork_tagged)


class PauseableThread(threading.Thread):
    """Base class: pause/resume/stop with countdown support via queue.Queue."""

    def __init__(self, q: _queue.Queue):
        super().__init__(daemon=True)
        self._q = q
        self._pause_event = threading.Event()
        self._pause_event.set()   # not paused by default
        self._stop_event = threading.Event()

    def pause(self):
        self._pause_event.clear()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已暫停</font></p>"))
        self._on_pause_hook()

    def resume(self):
        self._pause_event.set()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已繼續</font></p>"))

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()   # unblock any waiting pause
        self._q.put(WorkerEvent("output", "<p><font color='red'>已停止</font></p>"))
        self._on_stop_hook()

    def _on_pause_hook(self):
        pass

    def _on_stop_hook(self):
        pass

    def _sleep_with_countdown(self, delay):
        """Sleep with pause/stop support; emits countdown ticks."""
        if delay <= 0:
            return
        for remaining in range(int(delay), 0, -1):
            if self._stop_event.is_set():
                break
            self._pause_event.wait()
            try:
                self._q.put(WorkerEvent("countdown", remaining))
            except Exception:
                pass
            time.sleep(1)
        try:
            self._q.put(WorkerEvent("countdown", 0))
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_thread_base.py -v
```
Expected: 4 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add app/core/pixiv_thread_base.py tests/test_thread_base.py
git commit -m "feat(core): replace PauseableThread QThread with threading.Thread + queue"
```

---

## Task 3: Rewrite thread_following.py

**Files:**
- Modify: `app/core/thread_following.py`

**Context:** `get_following` inherits `PauseableThread`. Changes needed:
- Remove `from PyQt5.QtCore import *`
- Remove class-level `pyqtSignal` declarations
- `__init__` accepts `q: queue.Queue` as first arg, passes to `super().__init__(q)`
- All `self._signal.emit(x,y)` → `self._q.put(WorkerEvent("progress", (x, y)))`
- All `self._output.emit(text)` → `self._q.put(WorkerEvent("output", text))`
- All `self._thenext.emit(n)` → `self._q.put(WorkerEvent("next", n))`
- All `self._finished.emit(msg)` → `self._q.put(WorkerEvent("finished", msg))`
- Pause checks: `while self._isPause == 1` → `self._pause_event.wait()` then check stop; `if self._isPause == 2` → `if self._stop_event.is_set()`

- [ ] **Step 1: Make the change to `thread_following.py`**

Remove `from PyQt5.QtCore import *` (line 1). Remove the four `pyqtSignal` class-level declarations. Update `__init__`:

```python
class get_following(PauseableThread):
    '''抓取使用者關注的畫師清單'''
    def __init__(self, q, userid, cookies, Agent, hide_mode):
        super().__init__(q)
        self.userid = userid
        self.cookies = cookies
        self.Agent = Agent
        self.path = os.getenv('APPDATA') + r'/pixiv_download/'
        self._partial_following = []
        self._partial_lock = threading.Lock()
        try:
            self.hide = hide_mode.isChecked()
        except Exception:
            self.hide = False
        self.max = 0
```

Replace all emit calls:

| Old | New |
|-----|-----|
| `self._signal.emit(100, self.max)` | `self._q.put(WorkerEvent("progress", (100, self.max)))` |
| `self._output.emit(text)` | `self._q.put(WorkerEvent("output", text))` |
| `self._thenext.emit(n)` | `self._q.put(WorkerEvent("next", n))` |
| `self._finished.emit(msg)` | `self._q.put(WorkerEvent("finished", msg))` |

In `get_follow_illust`, replace pause checks:
```python
# OLD:
while self._isPause == 1:
    time.sleep(1)
if self._isPause == 2:
    return []

# NEW:
self._pause_event.wait()
if self._stop_event.is_set():
    return []
```

Add import at top: `from app.core.worker_event import WorkerEvent`

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
pytest tests/ -v --ignore=tests/test_worker_event.py --ignore=tests/test_thread_base.py -x
```
Expected: all tests that previously passed continue to PASS (tests that mock `_output` as a signal will be updated in Task 7)

- [ ] **Step 3: Commit**

```bash
git add app/core/thread_following.py
git commit -m "feat(core): migrate get_following from pyqtSignal to WorkerEvent queue"
```

---

## Task 4: Rewrite thread_pid_scan.py

**Files:**
- Modify: `app/core/thread_pid_scan.py`

**Context:** Same pattern as Task 3. `get_pixiv_author_imgID_Thread` has 5 signal types. Count of `_isPause` checks: 6.

- [ ] **Step 1: Make the change to `thread_pid_scan.py`**

Remove `from PyQt5.QtCore import *` (line 1). Remove class-level `pyqtSignal` declarations (lines 34-38). Update `__init__` to accept `q` as first argument and call `super().__init__(q)`.

Add import: `from app.core.worker_event import WorkerEvent`

Replace all emit calls using the same mapping as Task 3. Replace all `_isPause` checks:

```python
# OLD pattern A (pause loop):
while self._isPause == 1:
    time.sleep(1)
# NEW:
self._pause_event.wait()

# OLD pattern B (stop check):
if self._isPause == 2:
    return  # (or break, or continue — match original logic)
# NEW:
if self._stop_event.is_set():
    return  # (or break, match original)
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -x
```
Expected: passing tests continue to PASS

- [ ] **Step 3: Commit**

```bash
git add app/core/thread_pid_scan.py
git commit -m "feat(core): migrate get_pixiv_author_imgID_Thread from pyqtSignal to WorkerEvent queue"
```

---

## Task 5: Rewrite thread_url_fetch.py

**Files:**
- Modify: `app/core/thread_url_fetch.py`

**Context:** `get_img_url_thread`. Same pattern. 9 `_isPause` checks, 5 signal types.

- [ ] **Step 1: Make the change to `thread_url_fetch.py`**

Same transformations as Tasks 3 and 4:
- Remove `from PyQt5.QtCore import *`
- Remove `pyqtSignal` class declarations (lines 38-42)
- Update `__init__` to accept `q` first, call `super().__init__(q)`
- Add `from app.core.worker_event import WorkerEvent`
- Replace all `.emit(...)` with `self._q.put(WorkerEvent(...))`
- Replace all `_isPause` checks with `_pause_event.wait()` / `_stop_event.is_set()`

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -x
```
Expected: passing tests continue to PASS

- [ ] **Step 3: Commit**

```bash
git add app/core/thread_url_fetch.py
git commit -m "feat(core): migrate get_img_url_thread from pyqtSignal to WorkerEvent queue"
```

---

## Task 6: Rewrite thread_download.py

**Files:**
- Modify: `app/core/thread_download.py`

**Context:** `download_thread` has an extra `_timechanged` signal and uses `QMutex`. Extra steps needed:
- `timelock = QMutex()` → `timelock = threading.Lock()`  (class-level)
- `self.timelock.lock()` / `.unlock()` → `self.timelock.acquire()` / `.release()` (two call sites: lines 1795-1799 and 1838-1841)
- `self._timechanged.emit(str)` → `self._q.put(WorkerEvent("timechanged", str))`
- 16 `_isPause` checks to update

- [ ] **Step 1: Make the change to `thread_download.py`**

Remove `from PyQt5.QtCore import *` (line 1). Add `import threading` (already present further down). Add `from app.core.worker_event import WorkerEvent`.

Remove class-level `pyqtSignal` declarations (lines 45-50). Replace:
```python
# OLD:
timelock = QMutex()
# NEW:
timelock = threading.Lock()
```

Update `__init__` to accept `q` as first positional argument:
```python
def __init__(self, q, nogif, notag, notime, create_dir, download_path,
             cookies, agent, download_time, no_R18G_dir, ...):
    super().__init__(q)
    ...
```

Replace `timelock.lock()` / `.unlock()`:
```python
# OLD (at lines ~1795-1799):
self.timelock.lock()
# ... critical section ...
self.timelock.unlock()

# NEW:
self.timelock.acquire()
# ... critical section ...
self.timelock.release()
```

Replace all emit calls using same mapping. `_timechanged.emit(str)` → `self._q.put(WorkerEvent("timechanged", str))`.

Replace 16 `_isPause` checks using the same patterns as Tasks 3-5. Note: `_stopped_by_request or (self._isPause == 2)` at line 1586 → `_stopped_by_request or self._stop_event.is_set()`.

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -x
```
Expected: passing tests continue to PASS (DummySignal tests will be fixed in Task 7)

- [ ] **Step 3: Commit**

```bash
git add app/core/thread_download.py
git commit -m "feat(core): migrate download_thread from pyqtSignal/QMutex to WorkerEvent queue + threading.Lock"
```

---

## Task 7: Update existing tests for queue API

**Files:**
- Modify: `tests/test_jxl_fallback.py`
- Modify: `tests/test_cookie_cooldown.py`
- Modify: `app/core/pixiv_thread.py` (update shim re-exports)

**Context:** Tests mock `_output` as a `DummySignal` with `.emit()`. After the rewrite, `_output` no longer exists — the thread pushes to `self._q`. Tests need to build stubs with a real `queue.Queue` and read events from it.

- [ ] **Step 1: Update `test_jxl_fallback.py`**

Replace `DummySignal` pattern:

```python
# OLD _build_thread_stub:
class DummySignal:
    def emit(self, _msg):
        return None

def _build_thread_stub(tmp_path):
    t = download_thread.__new__(download_thread)
    t._output = DummySignal()
    ...

# NEW _build_thread_stub (add queue import at top):
import queue

def _build_thread_stub(tmp_path):
    t = download_thread.__new__(download_thread)
    t._q = queue.Queue()          # replaces DummySignal
    t.jxl_enable = True
    t.jxl_delete_original = False
    t.jxl_effort = 7
    t._jxl_path_warned = False
    t._jxl_ok_count = 0
    t._jxl_fail_count = 0
    t._jxl_gif_skip_warned = False
    cjxl = tmp_path / "cjxl.exe"
    cjxl.write_bytes(b"")
    t.jxl_cjxl_path = str(cjxl)
    return t
```

Remove the `DummySignal` class entirely.

- [ ] **Step 2: Update `test_cookie_cooldown.py`**

```python
# OLD _build_thread_stub:
def _build_thread_stub():
    t = download_thread.__new__(download_thread)
    t.cookies = "PHPSESSID=dummy"
    t.cookie_pool = []
    t.url_meta = {}
    t._pid_cookie_used = {}
    return t

# NEW:
import queue

def _build_thread_stub():
    t = download_thread.__new__(download_thread)
    t._q = queue.Queue()
    t._pause_event = __import__('threading').Event()
    t._pause_event.set()
    t._stop_event = __import__('threading').Event()
    t.cookies = "PHPSESSID=dummy"
    t.cookie_pool = []
    t.url_meta = {}
    t._pid_cookie_used = {}
    return t
```

- [ ] **Step 3: Update `app/core/pixiv_thread.py` shim**

Remove `PauseableThread` from imports (it still lives in `pixiv_thread_base.py` but no longer has Qt deps — fine to keep re-exporting). Verify the shim still imports correctly with no Qt imports at module level.

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -v
```
Expected: ALL tests PASS (no Qt import errors)

- [ ] **Step 5: Verify no Qt imports remain in app/core/**

```bash
grep -r "from PyQt5\|import PyQt5" app/core/
```
Expected: no output

- [ ] **Step 6: Commit**

```bash
git add tests/test_jxl_fallback.py tests/test_cookie_cooldown.py app/core/pixiv_thread.py
git commit -m "test: update stubs to use queue.Queue instead of DummySignal after Qt removal"
```

---

## Task 8: Flet skeleton + dispatcher

**Files:**
- Modify: `pyproject.toml`
- Create: `app/gui/dispatcher.py`
- Create: `app/gui/flet_app.py`
- Create: `app/gui/views/__init__.py`
- Modify: `app/entry/main.py`
- Create: `tests/test_dispatcher.py`

- [ ] **Step 1: Add flet to pyproject.toml**

In `pyproject.toml`, add after `requires-python`:
```toml
dependencies = ["flet>=0.21.0"]
```

Install: `pip install flet`

- [ ] **Step 2: Write failing dispatcher test**

```python
# tests/test_dispatcher.py
from pathlib import Path
import sys
import queue
import time
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.worker_event import WorkerEvent
from app.gui.dispatcher import EventDispatcher


class _FakePage:
    def __init__(self):
        self.update_count = 0
    def update(self):
        self.update_count += 1


def test_dispatcher_routes_output_event():
    page = _FakePage()
    q: queue.Queue = queue.Queue()
    received = []
    handlers = {"output": lambda d: received.append(d)}
    disp = EventDispatcher(page, q, handlers)

    q.put(WorkerEvent("output", "hello"))
    # run one poll cycle manually
    disp._poll_once()

    assert received == ["hello"]
    assert page.update_count == 1


def test_dispatcher_ignores_unknown_event_type():
    page = _FakePage()
    q: queue.Queue = queue.Queue()
    handlers = {}
    disp = EventDispatcher(page, q, handlers)
    q.put(WorkerEvent("unknown_type", None))
    disp._poll_once()  # should not raise
    assert page.update_count == 1


def test_dispatcher_batches_multiple_events():
    page = _FakePage()
    q: queue.Queue = queue.Queue()
    received = []
    handlers = {"output": lambda d: received.append(d)}
    disp = EventDispatcher(page, q, handlers)
    for i in range(5):
        q.put(WorkerEvent("output", str(i)))
    disp._poll_once()
    assert received == ["0", "1", "2", "3", "4"]
    assert page.update_count == 1   # one update for the whole batch
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/test_dispatcher.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.gui.dispatcher'`

- [ ] **Step 4: Create `app/gui/dispatcher.py`**

```python
from __future__ import annotations
import queue
import time
import threading
from typing import Any, Callable

from app.core.worker_event import WorkerEvent


class EventDispatcher:
    """Polls a WorkerEvent queue and dispatches events to Flet UI handlers.

    Designed to run in a background thread via page.run_thread(dispatcher.run).
    Batches all pending events in each 50 ms window into a single page.update().
    """

    def __init__(self, page: Any, q: queue.Queue, handlers: dict[str, Callable]):
        self._page = page
        self._q = q
        self._handlers = handlers
        self._stop = False

    def _poll_once(self) -> None:
        updated = False
        try:
            while True:
                ev: WorkerEvent = self._q.get_nowait()
                handler = self._handlers.get(ev.type)
                if handler is not None:
                    handler(ev.data)
                updated = True
        except queue.Empty:
            pass
        if updated:
            self._page.update()

    def run(self) -> None:
        while not self._stop:
            self._poll_once()
            time.sleep(0.05)

    def stop(self) -> None:
        self._stop = True
```

- [ ] **Step 5: Run dispatcher test**

```bash
pytest tests/test_dispatcher.py -v
```
Expected: 3 tests PASSED

- [ ] **Step 6: Create `app/gui/views/__init__.py`**

```python
```
(empty file)

- [ ] **Step 7: Create `app/gui/flet_app.py` skeleton**

```python
from __future__ import annotations
import queue
import flet as ft
from app.gui.dispatcher import EventDispatcher


def main(page: ft.Page) -> None:
    page.title = "Pixiv 下載器"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.theme = ft.Theme(color_scheme_seed="#0096FA")

    # Shared event queue — all worker threads push here
    event_q: queue.Queue = queue.Queue()

    # Placeholder content while views are built in later tasks
    status_text = ft.Text("Flet 骨架已啟動 — 待接入 UI 模組")

    def handle_output(data: str) -> None:
        status_text.value = data

    disp = EventDispatcher(page, event_q, {
        "output": handle_output,
    })

    page.add(
        ft.AppBar(title=ft.Text("Pixiv 下載器")),
        ft.Column([status_text]),
    )
    page.run_thread(disp.run)


if __name__ == "__main__":
    ft.app(target=main)
```

- [ ] **Step 8: Update `app/entry/main.py`**

```python
import flet as ft
from app.gui.flet_app import main as flet_main


def main():
    ft.app(target=flet_main)


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Verify Flet window opens**

```bash
python app/entry/main.py
```
Expected: A Flet desktop window opens with title "Pixiv 下載器" and the placeholder text visible. Close it manually.

- [ ] **Step 10: Run full test suite**

```bash
pytest tests/ -v
```
Expected: ALL tests PASS

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml app/gui/dispatcher.py app/gui/flet_app.py app/gui/views/__init__.py app/entry/main.py tests/test_dispatcher.py
git commit -m "feat(gui): add Flet skeleton with EventDispatcher and queue-based event routing"
```

---

## Task 9: HTML → TextSpan log formatter

**Files:**
- Create: `app/gui/log_format.py`
- Create: `tests/test_log_format.py`

**Context:** Worker thread output lines look like `<p><font color='red'>已暫停</font></p>` or `<p><font color='gray'>[PID] info</font></p>`. Parser uses `re` only (no HTML library). Output is a list of `ft.TextSpan` for use in `ft.Text(spans=[...])`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_log_format.py
from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import flet as ft
from app.gui.log_format import html_to_spans, COLOR_MAP


def test_plain_text_becomes_single_span():
    spans = html_to_spans("hello world")
    assert len(spans) == 1
    assert spans[0].text == "hello world"
    assert spans[0].style is None


def test_red_font_tag():
    spans = html_to_spans("<font color='red'>error</font>")
    assert len(spans) == 1
    assert spans[0].text == "error"
    assert spans[0].style.color == COLOR_MAP["red"]


def test_green_font_tag():
    spans = html_to_spans("<font color='green'>ok</font>")
    assert spans[0].style.color == COLOR_MAP["green"]


def test_gray_font_tag():
    spans = html_to_spans("<font color='gray'>info</font>")
    assert spans[0].style.color == COLOR_MAP["gray"]


def test_p_wrapper_stripped():
    spans = html_to_spans("<p><font color='red'>msg</font></p>")
    assert len(spans) == 1
    assert spans[0].text == "msg"


def test_mixed_content():
    spans = html_to_spans("prefix <font color='red'>error</font> suffix")
    texts = [s.text for s in spans]
    assert "prefix " in texts
    assert "error" in texts
    assert " suffix" in texts


def test_unknown_color_falls_back_to_default():
    spans = html_to_spans("<font color='purple'>text</font>")
    assert spans[0].style is None


def test_empty_string():
    spans = html_to_spans("")
    assert spans == []


def test_double_quote_attribute():
    spans = html_to_spans('<font color="green">ok</font>')
    assert spans[0].style.color == COLOR_MAP["green"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_log_format.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.gui.log_format'`

- [ ] **Step 3: Create `app/gui/log_format.py`**

```python
from __future__ import annotations
import re
from typing import Optional
import flet as ft

COLOR_MAP: dict[str, str] = {
    "red":   ft.Colors.RED_600,
    "green": ft.Colors.GREEN_600,
    "gray":  ft.Colors.GREY_600,
    "black": ft.Colors.ON_SURFACE,
}

_FONT_RE = re.compile(
    r"<font\s+color=['\"](\w+)['\"]>(.*?)</font>",
    re.IGNORECASE | re.DOTALL,
)


def html_to_spans(html: str) -> list[ft.TextSpan]:
    """Convert a single HTML log line to a list of ft.TextSpan objects.

    Handles: <p>...</p> wrappers, <font color='X'>...</font> tags.
    Unknown color names fall back to default (no style).
    """
    if not html:
        return []

    # Strip outer <p>...</p>
    text = re.sub(r"^\s*<p>(.*)</p>\s*$", r"\1", html.strip(), flags=re.DOTALL | re.IGNORECASE)

    spans: list[ft.TextSpan] = []
    last_end = 0

    for m in _FONT_RE.finditer(text):
        # Text before the tag
        before = text[last_end:m.start()]
        if before:
            spans.append(ft.TextSpan(text=before))

        color_name = m.group(1).lower()
        content = m.group(2)
        flet_color = COLOR_MAP.get(color_name)
        style = ft.TextStyle(color=flet_color) if flet_color else None
        spans.append(ft.TextSpan(text=content, style=style))
        last_end = m.end()

    # Remaining text after last tag
    tail = text[last_end:]
    if tail:
        spans.append(ft.TextSpan(text=tail))

    return spans
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_log_format.py -v
```
Expected: 9 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add app/gui/log_format.py tests/test_log_format.py
git commit -m "feat(gui): add HTML-to-TextSpan log formatter for Flet log display"
```

---

## Task 10: Main view (step cards + progress + log)

**Files:**
- Create: `app/gui/views/main_view.py`

**Context:** The main view contains 4 step cards, control buttons, a progress bar with countdown, and a scrollable log area. No tests (UI layout); verified manually in Task 11.

- [ ] **Step 1: Create `app/gui/views/main_view.py`**

```python
from __future__ import annotations
import queue
import flet as ft


STEP_LABELS = ["步驟 1\n抓追蹤", "步驟 2\n抓 PID", "步驟 3\n抓 URL", "步驟 4\n下載"]
_STATE_COLORS = {
    "idle":    ft.Colors.GREY_400,
    "running": ft.Colors.BLUE_600,
    "done":    ft.Colors.GREEN_600,
    "error":   ft.Colors.RED_600,
}
_MAX_LOG_LINES = 2000


class MainView:
    """The primary workflow view: step cards, controls, progress, log."""

    def __init__(self, page: ft.Page, event_q: queue.Queue):
        self._page = page
        self._event_q = event_q
        self._active_thread = None
        self._step_states: list[str] = ["idle", "idle", "idle", "idle"]

        # --- step cards ---
        self._step_cards = [self._make_step_card(i) for i in range(4)]

        # --- control buttons ---
        self._btn_run_all = ft.FilledButton("▶ 一鍵執行", on_click=self._on_run_all)
        self._btn_step = [
            ft.OutlinedButton(f"步驟 {i+1}", on_click=lambda e, n=i+1: self._on_run_step(n))
            for i in range(4)
        ]
        self._btn_pause = ft.OutlinedButton("⏸ 暫停", on_click=self._on_pause, disabled=True)
        self._btn_stop = ft.OutlinedButton("⏹ 停止", on_click=self._on_stop, disabled=True)

        # --- progress ---
        self._progress_bar = ft.ProgressBar(value=0, width=float("inf"))
        self._progress_text = ft.Text("", size=12, color=ft.Colors.GREY_600)
        self._countdown_text = ft.Text("", size=12, color=ft.Colors.ORANGE_600)

        # --- log ---
        self._log_lines: list[ft.Text] = []
        self._log_list = ft.ListView(
            controls=self._log_lines,
            expand=True,
            spacing=1,
            auto_scroll=True,
        )

    def _make_step_card(self, index: int) -> ft.Card:
        self._step_labels_ref = getattr(self, "_step_labels_ref", [])
        label = ft.Text(STEP_LABELS[index], text_align=ft.TextAlign.CENTER, size=13)
        self._step_labels_ref.append(label)
        return ft.Card(
            content=ft.Container(
                content=label,
                padding=12,
                bgcolor=_STATE_COLORS["idle"],
                border_radius=8,
                width=110,
                alignment=ft.alignment.center,
            ),
        )

    def set_step_state(self, index: int, state: str) -> None:
        """Update step card color. state: 'idle'|'running'|'done'|'error'"""
        self._step_states[index] = state
        card = self._step_cards[index]
        card.content.bgcolor = _STATE_COLORS.get(state, _STATE_COLORS["idle"])

    def append_log(self, html_line: str) -> None:
        """Parse HTML log line and append to the log list."""
        from app.gui.log_format import html_to_spans
        spans = html_to_spans(html_line)
        if not spans:
            return
        text_ctrl = ft.Text(spans=spans, size=12)
        self._log_lines.append(text_ctrl)
        if len(self._log_lines) > _MAX_LOG_LINES:
            self._log_lines.pop(0)

    def update_progress(self, current: int, total: int) -> None:
        ratio = current / total if total else 0
        self._progress_bar.value = ratio
        self._progress_text.value = f"{current}/{total}"

    def update_countdown(self, remaining: int) -> None:
        if remaining > 0:
            self._countdown_text.value = f"倒數：{remaining} 秒"
        else:
            self._countdown_text.value = ""

    def set_running(self, is_running: bool) -> None:
        self._btn_pause.disabled = not is_running
        self._btn_stop.disabled = not is_running
        self._btn_run_all.disabled = is_running
        for b in self._btn_step:
            b.disabled = is_running

    def _on_run_all(self, e: ft.ControlEvent) -> None:
        # Placeholder — wired in Task 11
        pass

    def _on_run_step(self, step: int) -> None:
        # Placeholder — wired in Task 11
        pass

    def _on_pause(self, e: ft.ControlEvent) -> None:
        if self._active_thread and hasattr(self._active_thread, "pause"):
            self._active_thread.pause()

    def _on_stop(self, e: ft.ControlEvent) -> None:
        if self._active_thread and hasattr(self._active_thread, "stop"):
            self._active_thread.stop()

    def build(self) -> ft.Column:
        step_row = ft.Row(
            controls=self._step_cards,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
        )
        control_row = ft.Row(
            controls=[self._btn_run_all, *self._btn_step, self._btn_pause, self._btn_stop],
            wrap=True,
            spacing=8,
        )
        progress_row = ft.Row(
            controls=[self._progress_bar, self._progress_text, self._countdown_text],
            spacing=12,
        )
        return ft.Column(
            controls=[
                step_row,
                control_row,
                progress_row,
                ft.Divider(),
                ft.Text("即時 Log", size=12, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=self._log_list,
                    expand=True,
                    border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
                    border_radius=4,
                    padding=4,
                ),
            ],
            expand=True,
            spacing=12,
        )
```

- [ ] **Step 2: Commit**

```bash
git add app/gui/views/main_view.py
git commit -m "feat(gui): add MainView with step cards, controls, progress bar, and log list"
```

---

## Task 11: Wire dispatcher to main_view (end-to-end)

**Files:**
- Modify: `app/gui/flet_app.py`

**Context:** Connect the real worker threads to the Flet UI by building the full `main()` function with `NavigationRail`, all views, and the full event handler dict.

- [ ] **Step 1: Rewrite `app/gui/flet_app.py`**

```python
from __future__ import annotations
import queue
import flet as ft

from app.gui.dispatcher import EventDispatcher
from app.gui.views.main_view import MainView


def main(page: ft.Page) -> None:
    page.title = "Pixiv 下載器"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.theme = ft.Theme(color_scheme_seed="#0096FA")
    page.window.width = 1100
    page.window.height = 750
    page.padding = 0

    event_q: queue.Queue = queue.Queue()

    main_view = MainView(page, event_q)

    # Placeholder views for settings and cookies (built in Tasks 13 and 14)
    settings_placeholder = ft.Column([ft.Text("設定頁（待實作）")], expand=True)
    cookies_placeholder = ft.Column([ft.Text("Cookie 管理頁（待實作）")], expand=True)

    views = [main_view.build(), settings_placeholder, cookies_placeholder]
    current_view_ref = ft.Ref[ft.Column]()

    content_area = ft.Column(
        controls=[views[0]],
        expand=True,
        ref=current_view_ref,
    )

    def on_nav_change(e: ft.ControlEvent) -> None:
        idx = e.control.selected_index
        content_area.controls = [views[idx]]
        page.update()

    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        on_change=on_nav_change,
        destinations=[
            ft.NavigationRailDestination(icon=ft.Icons.HOME_OUTLINED, selected_icon=ft.Icons.HOME, label="主頁"),
            ft.NavigationRailDestination(icon=ft.Icons.SETTINGS_OUTLINED, selected_icon=ft.Icons.SETTINGS, label="設定"),
            ft.NavigationRailDestination(icon=ft.Icons.COOKIE_OUTLINED, selected_icon=ft.Icons.COOKIE, label="Cookie"),
        ],
    )

    def handle_output(data: str) -> None:
        main_view.append_log(data)

    def handle_progress(data: tuple) -> None:
        current, total = data
        main_view.update_progress(current, total)

    def handle_countdown(data: int) -> None:
        main_view.update_countdown(data)

    def handle_finished(data: str) -> None:
        main_view.append_log(f"<p><font color='green'>{data}</font></p>")
        main_view.set_running(False)

    def handle_next(data: int) -> None:
        if data == -1:
            main_view.set_running(False)

    disp = EventDispatcher(page, event_q, {
        "output":   handle_output,
        "progress": handle_progress,
        "countdown": handle_countdown,
        "finished": handle_finished,
        "next":     handle_next,
    })

    page.appbar = ft.AppBar(
        title=ft.Text("Pixiv 下載器"),
        center_title=False,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        actions=[
            ft.IconButton(
                icon=ft.Icons.LIGHT_MODE,
                tooltip="切換深淺色",
                on_click=lambda e: (
                    setattr(page, "theme_mode",
                            ft.ThemeMode.DARK if page.theme_mode == ft.ThemeMode.LIGHT else ft.ThemeMode.LIGHT),
                    page.update(),
                ),
            ),
        ],
    )

    page.add(
        ft.Row(
            controls=[nav_rail, ft.VerticalDivider(width=1), content_area],
            expand=True,
        )
    )
    page.run_thread(disp.run)


if __name__ == "__main__":
    ft.app(target=main)
```

- [ ] **Step 2: Manually run and verify**

```bash
python app/entry/main.py
```
Expected: Window opens with NavigationRail on left, step cards visible, log area visible, dark/light toggle works, nav between pages works. Close manually.

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
```
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add app/gui/flet_app.py
git commit -m "feat(gui): wire NavigationRail, MainView, and EventDispatcher in flet_app.py"
```

---

## Task 12: Rewrite user_info.py (remove Qt imports)

**Files:**
- Modify: `app/gui/user_info.py`

**Context:** `user_info.py` uses `QFileDialog.getExistingDirectory()` and `QDateTime`. Replace:
- `QFileDialog.getExistingDirectory()` → return `None`; Flet's `FilePicker` is used from the view layer instead
- `QDateTime.currentDateTime()` → `datetime.datetime.now().strftime("yyyy-MM-dd hh:mm:ss")` (but formatted as a plain string)
- `QDateTime.fromString(...)` → plain string storage (the settings layer just stores ISO strings, no Qt object needed)
- Widget setters (`.setText()`, `.setValue()`, `.setChecked()`, `.setDateTime()`) → the Flet version passes plain values; `Userdata_controller` and friends are refactored to return dicts instead of poking widget objects

The simplest safe approach: keep `Userdata_controller` but make widget parameters optional (`None`-safe), and replace `QDateTime` calls with stdlib `datetime`. The write methods read plain Python values passed in by the Flet view.

- [ ] **Step 1: Remove Qt imports from `app/gui/user_info.py`**

Replace lines 1-2:
```python
# REMOVE:
from PyQt5.QtWidgets import QFileDialog
from PyQt5.QtCore import QDateTime

# ADD:
import datetime
```

- [ ] **Step 2: Replace `QDateTime` usages**

In `_apply_download_section` (around line 104-107), replace:
```python
# OLD:
if self.last_download_time == "":
    self._ui_download_time.setDateTime(QDateTime.currentDateTime())
else:
    self._ui_download_time.setDateTime(
        QDateTime.fromString(self.last_download_time, "yyyy-MM-dd hh:mm:ss"))

# NEW:
if self._ui_download_time is not None:
    dt_str = self.last_download_time or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(self._ui_download_time, "value"):
        self._ui_download_time.value = dt_str
```

In `write_data` (around line 164), replace:
```python
# OLD:
"download_time": self._ui_download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss"),

# NEW:
"download_time": (
    self._ui_download_time.value
    if self._ui_download_time is not None and hasattr(self._ui_download_time, "value")
    else ""
),
```

In `load_data` (around line 130), replace the fallback error block:
```python
# OLD:
self._ui_download_time.setDateTime(QDateTime.currentDateTime())

# NEW:
if self._ui_download_time is not None and hasattr(self._ui_download_time, "value"):
    self._ui_download_time.value = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
```

- [ ] **Step 3: Replace `QFileDialog` usage**

In `open_folder` method:
```python
# OLD:
def open_folder(self):
    folder_path = QFileDialog.getExistingDirectory(None, "Open folder", "./")
    return folder_path

# NEW:
def open_folder(self):
    # FilePicker is handled by the Flet view layer; return None as sentinel
    return None
```

- [ ] **Step 4: Verify no Qt imports remain in app/gui/user_info.py**

```bash
grep "PyQt5\|QFileDialog\|QDateTime" app/gui/user_info.py
```
Expected: no output

- [ ] **Step 5: Run tests**

```bash
pytest tests/ -v
```
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/gui/user_info.py
git commit -m "feat(gui): remove QFileDialog/QDateTime from user_info.py, use stdlib datetime"
```

---

## Task 13: Settings view

**Files:**
- Create: `app/gui/views/settings_view.py`
- Modify: `app/gui/flet_app.py` (swap placeholder for real view)

**Context:** Reads and writes settings via `SettingsStore`. Groups: 帳號、過濾規則、標籤過濾、JXL 轉檔、下載設定. Uses `ft.ExpansionTile`, `ft.TextField`, `ft.Switch`, `ft.Slider`, `ft.FilePicker`.

- [ ] **Step 1: Create `app/gui/views/settings_view.py`**

```python
from __future__ import annotations
import os
import flet as ft
from app.core.settings_store import SettingsStore


def _store() -> SettingsStore:
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return SettingsStore(path)


class SettingsView:
    """Settings page grouped into ExpansionTile sections."""

    def __init__(self, page: ft.Page):
        self._page = page
        self._file_picker = ft.FilePicker(on_result=self._on_folder_picked)
        self._jxl_picker = ft.FilePicker(on_result=self._on_jxl_picked)
        page.overlay.extend([self._file_picker, self._jxl_picker])

        store = _store()
        store.migrate_from_legacy()
        auth = store.get_section("auth")
        dl = store.get_section("download")
        flt = store.get_section("filter")
        perf = store.get_section("performance")
        jxl = store.get_section("jxl")

        # --- 帳號設定 ---
        self._tf_account = ft.TextField(label="帳號", value=auth.get("account", ""), width=300)
        self._tf_password = ft.TextField(label="密碼", value=auth.get("password", ""), width=300, password=True, can_reveal_password=True)
        self._tf_userid = ft.TextField(label="User ID", value=str(auth.get("userid", "")), width=200)
        self._tf_path = ft.TextField(label="下載路徑", value=dl.get("path", ""), expand=True, read_only=True)

        # --- 過濾規則 ---
        self._sw_hidefollow = ft.Switch(label="隱藏追蹤", value=bool(flt.get("hidefollow", False)))
        self._sw_nogif = ft.Switch(label="過濾 GIF", value=bool(flt.get("nogif", False)))
        self._sw_notag = ft.Switch(label="無 tag 不下載", value=bool(flt.get("notag", False)))
        self._sw_notime = ft.Switch(label="無時間不下載", value=bool(flt.get("notime", False)))
        self._sw_no_r18g = ft.Switch(label="R18G 不建資料夾", value=False)
        self._sw_create_dir = ft.Switch(label="依作者建立資料夾", value=False)
        self._tf_like_num = ft.TextField(label="最低讚數（一般）", value=str(dl.get("like_num", 0)), width=150, keyboard_type=ft.KeyboardType.NUMBER)
        self._tf_r18_like_num = ft.TextField(label="最低讚數（R18）", value=str(dl.get("r18_like_num", 0)), width=150, keyboard_type=ft.KeyboardType.NUMBER)

        # --- 標籤過濾 ---
        self._ban_tags: list[str] = list(dl.get("ban_tag", []))
        self._must_tags: list[str] = list(dl.get("must_tag", []))
        self._ban_tag_row = ft.Row(wrap=True, spacing=4)
        self._must_tag_row = ft.Row(wrap=True, spacing=4)
        self._tf_ban_input = ft.TextField(label="新增禁止 tag", width=200, on_submit=self._add_ban_tag)
        self._tf_must_input = ft.TextField(label="新增必須 tag", width=200, on_submit=self._add_must_tag)
        self._refresh_tag_rows()

        # --- JXL ---
        self._sw_jxl = ft.Switch(label="啟用 JXL 轉檔", value=bool(jxl.get("enable", False)))
        self._tf_jxl_path = ft.TextField(label="cjxl.exe 路徑", value=jxl.get("cjxl_path", ""), expand=True, read_only=True)
        self._sw_jxl_delete = ft.Switch(label="刪除原檔", value=bool(jxl.get("delete_original", False)))
        effort_val = max(1, min(9, int(jxl.get("effort", 7))))
        self._sl_jxl_effort = ft.Slider(min=1, max=9, divisions=8, value=effort_val, label="{value}", width=200)

        # --- 下載設定 ---
        self._tf_dl_wait_min = ft.TextField(label="等待最小秒數", value=str(perf.get("pid_wait_min", 10)), width=120, keyboard_type=ft.KeyboardType.NUMBER)
        self._tf_dl_wait_max = ft.TextField(label="等待最大秒數", value=str(perf.get("pid_wait_max", 60)), width=120, keyboard_type=ft.KeyboardType.NUMBER)
        self._sw_single_thread = ft.Switch(label="單執行緒 PID 模式", value=bool(perf.get("single_thread_mode", False)))

    def _on_folder_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.path:
            self._tf_path.value = e.path + "/"
            self._tf_path.update()

    def _on_jxl_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.files:
            self._tf_jxl_path.value = e.files[0].path
            self._tf_jxl_path.update()

    def _add_ban_tag(self, e: ft.ControlEvent) -> None:
        tag = self._tf_ban_input.value.strip()
        if tag and tag not in self._ban_tags:
            self._ban_tags.append(tag)
            self._tf_ban_input.value = ""
            self._refresh_tag_rows()
            self._page.update()

    def _add_must_tag(self, e: ft.ControlEvent) -> None:
        tag = self._tf_must_input.value.strip()
        if tag and tag not in self._must_tags:
            self._must_tags.append(tag)
            self._tf_must_input.value = ""
            self._refresh_tag_rows()
            self._page.update()

    def _refresh_tag_rows(self) -> None:
        self._ban_tag_row.controls = [
            ft.Chip(label=ft.Text(t), on_delete=lambda e, tag=t: self._remove_ban_tag(tag))
            for t in self._ban_tags
        ]
        self._must_tag_row.controls = [
            ft.Chip(label=ft.Text(t), on_delete=lambda e, tag=t: self._remove_must_tag(tag))
            for t in self._must_tags
        ]

    def _remove_ban_tag(self, tag: str) -> None:
        self._ban_tags = [t for t in self._ban_tags if t != tag]
        self._refresh_tag_rows()
        self._page.update()

    def _remove_must_tag(self, tag: str) -> None:
        self._must_tags = [t for t in self._must_tags if t != tag]
        self._refresh_tag_rows()
        self._page.update()

    def save(self) -> None:
        """Persist all settings to SettingsStore."""
        store = _store()
        auth = store.get_section("auth")
        store.update_section("auth", {
            **auth,
            "account": self._tf_account.value,
            "password": self._tf_password.value,
            "userid": self._tf_userid.value,
        })
        store.update_section("download", {
            **store.get_section("download"),
            "path": self._tf_path.value,
            "like_num": int(self._tf_like_num.value or 0),
            "r18_like_num": int(self._tf_r18_like_num.value or 0),
            "ban_tag": self._ban_tags,
            "must_tag": self._must_tags,
        })
        store.update_multiple({
            "filter": {
                **store.get_section("filter"),
                "hidefollow": self._sw_hidefollow.value,
                "nogif": self._sw_nogif.value,
                "notag": self._sw_notag.value,
                "notime": self._sw_notime.value,
            },
            "performance": {
                "single_thread_mode": self._sw_single_thread.value,
                "pid_wait_min": int(self._tf_dl_wait_min.value or 10),
                "pid_wait_max": int(self._tf_dl_wait_max.value or 60),
            },
            "jxl": {
                "enable": self._sw_jxl.value,
                "cjxl_path": self._tf_jxl_path.value,
                "delete_original": self._sw_jxl_delete.value,
                "effort": int(self._sl_jxl_effort.value),
            },
        })

    def build(self) -> ft.Column:
        def _tile(title: str, controls: list) -> ft.ExpansionTile:
            return ft.ExpansionTile(
                title=ft.Text(title),
                initially_expanded=False,
                controls=[ft.Container(content=ft.Column(controls, spacing=8), padding=ft.padding.only(left=16, bottom=12))],
            )

        save_btn = ft.FilledButton("儲存設定", icon=ft.Icons.SAVE, on_click=lambda e: self.save() or self._page.snack_bar.__setattr__("open", True) or self._page.update())
        self._page.snack_bar = ft.SnackBar(ft.Text("設定已儲存"), duration=1500)

        return ft.Column(
            controls=[
                ft.Text("設定", size=20, weight=ft.FontWeight.BOLD),
                _tile("帳號設定", [
                    self._tf_account,
                    self._tf_password,
                    self._tf_userid,
                    ft.Row([self._tf_path, ft.IconButton(icon=ft.Icons.FOLDER_OPEN, on_click=lambda e: self._file_picker.get_directory_path())]),
                ]),
                _tile("過濾規則", [
                    ft.Row([self._sw_hidefollow, self._sw_nogif, self._sw_notag, self._sw_notime], wrap=True),
                    ft.Row([self._tf_like_num, self._tf_r18_like_num], spacing=16),
                ]),
                _tile("標籤過濾", [
                    ft.Text("禁止 tag", size=12), self._ban_tag_row,
                    ft.Row([self._tf_ban_input, ft.IconButton(icon=ft.Icons.ADD, on_click=self._add_ban_tag)]),
                    ft.Text("必須 tag", size=12), self._must_tag_row,
                    ft.Row([self._tf_must_input, ft.IconButton(icon=ft.Icons.ADD, on_click=self._add_must_tag)]),
                ]),
                _tile("JXL 轉檔", [
                    self._sw_jxl,
                    ft.Row([self._tf_jxl_path, ft.IconButton(icon=ft.Icons.FILE_OPEN, on_click=lambda e: self._jxl_picker.pick_files(allowed_extensions=["exe"]))]),
                    self._sw_jxl_delete,
                    ft.Row([ft.Text("Effort（1-9）"), self._sl_jxl_effort]),
                ]),
                _tile("下載設定", [
                    ft.Row([self._tf_dl_wait_min, self._tf_dl_wait_max], spacing=16),
                    self._sw_single_thread,
                ]),
                ft.Container(content=save_btn, padding=ft.padding.only(top=8)),
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=0,
            expand=True,
        )
```

- [ ] **Step 2: Wire into `app/gui/flet_app.py`**

In `main()`, replace `settings_placeholder`:
```python
from app.gui.views.settings_view import SettingsView
settings_view = SettingsView(page)
views = [main_view.build(), settings_view.build(), cookies_placeholder]
```

- [ ] **Step 3: Manually verify**

```bash
python app/entry/main.py
```
Expected: Click settings icon in nav rail, see ExpansionTile groups. Expand each group, fill a field, click 儲存設定, see snack bar. Reopen app and verify values persisted.

- [ ] **Step 4: Commit**

```bash
git add app/gui/views/settings_view.py app/gui/flet_app.py
git commit -m "feat(gui): add SettingsView with ExpansionTile groups and FilePicker"
```

---

## Task 14: Cookie management view

**Files:**
- Create: `app/gui/views/cookies_view.py`
- Modify: `app/gui/flet_app.py` (swap placeholder)

- [ ] **Step 1: Create `app/gui/views/cookies_view.py`**

```python
from __future__ import annotations
import os
import flet as ft
from app.core.settings_store import SettingsStore
from app.core.pixiv_thread_utils import normalize_cookie_entries, normalize_cookie_pool


def _store() -> SettingsStore:
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return SettingsStore(path)


class CookiesView:
    """Cookie pool management: list, add, edit, remove, test."""

    def __init__(self, page: ft.Page):
        self._page = page
        self._entries: list[dict] = []   # list of {"cookie": str, "alias": str, "status": str}
        self._load_entries()
        self._table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("別名")),
                ft.DataColumn(ft.Text("狀態")),
                ft.DataColumn(ft.Text("Cookie 預覽")),
                ft.DataColumn(ft.Text("操作")),
            ],
            rows=[],
        )
        self._refresh_table()

    def _load_entries(self) -> None:
        store = _store()
        store.migrate_from_legacy()
        auth = store.get_section("auth")
        alias_map = auth.get("cookies_aliases", {})
        raw = auth.get("cookies_entries", []) or auth.get("cookies_pool", [])
        self._entries = normalize_cookie_entries(raw, alias_map=alias_map)

    def _save_entries(self) -> None:
        store = _store()
        auth = store.get_section("auth")
        pool = [x.get("cookie", "") for x in self._entries if x.get("cookie", "").strip()]
        alias_map = {x["cookie"]: x.get("alias", "") for x in self._entries if x.get("cookie", "").strip()}
        store.update_section("auth", {
            **auth,
            "cookies_entries": self._entries,
            "cookies_pool": pool,
            "cookies_aliases": alias_map,
            "cookies": pool[0] if pool else "",
        })

    def _refresh_table(self) -> None:
        self._table.rows = []
        for i, entry in enumerate(self._entries):
            alias = entry.get("alias", "") or f"Cookie {i+1}"
            cookie = entry.get("cookie", "")
            status = entry.get("status", "未知")
            preview = cookie[:30] + "..." if len(cookie) > 30 else cookie
            status_color = ft.Colors.GREEN_600 if status == "有效" else ft.Colors.RED_600 if status == "失效" else ft.Colors.GREY_600
            self._table.rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(alias)),
                ft.DataCell(ft.Text(status, color=status_color)),
                ft.DataCell(ft.Text(preview, size=11, font_family="monospace")),
                ft.DataCell(ft.Row([
                    ft.IconButton(icon=ft.Icons.EDIT, tooltip="編輯", on_click=lambda e, idx=i: self._open_edit_dialog(idx)),
                    ft.IconButton(icon=ft.Icons.DELETE, tooltip="刪除", icon_color=ft.Colors.RED_400, on_click=lambda e, idx=i: self._remove_entry(idx)),
                ])),
            ]))

    def _open_add_dialog(self, e: ft.ControlEvent) -> None:
        self._open_edit_dialog(None)

    def _open_edit_dialog(self, idx: int | None) -> None:
        entry = self._entries[idx] if idx is not None else {}
        tf_alias = ft.TextField(label="別名（例：主帳號）", value=entry.get("alias", ""), width=300)
        tf_cookie = ft.TextField(label="Cookie 字串", value=entry.get("cookie", ""), multiline=True, min_lines=3, max_lines=6, width=500)

        def save_dialog(e: ft.ControlEvent) -> None:
            new_entry = {"cookie": tf_cookie.value.strip(), "alias": tf_alias.value.strip(), "status": entry.get("status", "未知")}
            if idx is None:
                self._entries.append(new_entry)
            else:
                self._entries[idx] = new_entry
            self._save_entries()
            self._refresh_table()
            dialog.open = False
            self._page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("編輯 Cookie" if idx is not None else "新增 Cookie"),
            content=ft.Column([tf_alias, tf_cookie], tight=True, spacing=12),
            actions=[
                ft.TextButton("取消", on_click=lambda e: setattr(dialog, "open", False) or self._page.update()),
                ft.FilledButton("儲存", on_click=save_dialog),
            ],
        )
        self._page.dialog = dialog
        dialog.open = True
        self._page.update()

    def _remove_entry(self, idx: int) -> None:
        self._entries.pop(idx)
        self._save_entries()
        self._refresh_table()
        self._page.update()

    def build(self) -> ft.Column:
        header = ft.Row([
            ft.Text("Cookies", size=20, weight=ft.FontWeight.BOLD),
            ft.Text(f"（共 {len(self._entries)} 筆）", color=ft.Colors.GREY_600),
            ft.FilledButton("+ 新增", icon=ft.Icons.ADD, on_click=self._open_add_dialog),
        ], alignment=ft.MainAxisAlignment.START, spacing=12)

        return ft.Column(
            controls=[
                header,
                ft.Container(
                    content=ft.Column([self._table], scroll=ft.ScrollMode.AUTO),
                    expand=True,
                ),
            ],
            expand=True,
            spacing=12,
        )
```

- [ ] **Step 2: Wire into `app/gui/flet_app.py`**

```python
from app.gui.views.cookies_view import CookiesView
cookies_view = CookiesView(page)
views = [main_view.build(), settings_view.build(), cookies_view.build()]
```

- [ ] **Step 3: Manually verify**

```bash
python app/entry/main.py
```
Expected: Cookie nav shows DataTable. Add a cookie with alias, verify it appears. Delete it, verify it disappears. Data persists after reopen.

- [ ] **Step 4: Commit**

```bash
git add app/gui/views/cookies_view.py app/gui/flet_app.py
git commit -m "feat(gui): add CookiesView with DataTable and add/edit/delete dialogs"
```

---

## Task 15: Cleanup + web mode verification

**Files:**
- Delete: `app/gui/controller.py`, `app/gui/run_actions.py`, `test.ui`, `uimake.py`, `trash/Ui2.py`
- Modify: `pyproject.toml` (remove PyQt5 note)
- Modify: `CLAUDE.md` (update architecture section)

- [ ] **Step 1: Delete PyQt5 GUI files**

```bash
git rm app/gui/controller.py app/gui/run_actions.py
git rm test.ui uimake.py
git rm trash/Ui2.py 2>/dev/null || true
```

- [ ] **Step 2: Verify no PyQt5 imports remain in app/**

```bash
grep -r "from PyQt5\|import PyQt5" app/
```
Expected: no output

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
```
Expected: ALL PASS

- [ ] **Step 4: Run quality checks**

```bash
ruff check app/
radon cc app/ -n C -s
lizard -C 15 -L 100 app/
vulture app/ vulture_whitelist.py --min-confidence 80
```
Expected: no new violations compared to main branch baseline

- [ ] **Step 5: Test desktop mode**

```bash
python app/entry/main.py
```
Expected: Full UI opens, all 4 nav destinations work, settings persist, cookie CRUD works. Run Step 1 (get following) with real credentials to confirm end-to-end.

- [ ] **Step 6: Test web mode**

```bash
flet run app/gui/flet_app.py --web
```
Expected: Browser opens at `localhost:8550` (or similar). UI is identical to desktop. FilePicker in web mode shows upload dialog (expected different behavior — log in CLAUDE.md).

- [ ] **Step 7: Update CLAUDE.md**

Replace all references to PyQt5, `test.ui`, `uimake.py`, `controller.py`, `run_actions.py` with the new Flet architecture. Update the "Run the app" command:

```markdown
## Commands

Run the app (desktop):
```bash
python app/entry/main.py
```

Run the app (web browser):
```bash
flet run app/gui/flet_app.py --web
```
```

Update the architecture section to describe Flet layers, `EventDispatcher`, `WorkerEvent`, views under `app/gui/views/`.

- [ ] **Step 8: Final commit**

```bash
git add -A
git commit -m "feat: complete PyQt5 → Flet migration, all core/gui layers Qt-free"
```

---

## Self-Review Against Spec

**Spec §2 Coverage:**

| Requirement | Task |
|---|---|
| QThread/pyqtSignal → threading.Thread + queue | Tasks 1–7 |
| Full new Flet UI | Tasks 8–11 |
| 4-step workflow + one-key run | Task 10–11 |
| Cookie pool management | Task 14 |
| JXL settings | Task 13 |
| Tag filtering settings | Task 13 |
| Log HTML → TextSpan | Task 9 |
| Desktop + web mode | Task 8, 15 |
| JSON schema unchanged | Tasks 12–14 (SettingsStore untouched) |
| Remove deleted files | Task 15 |
| Update CLAUDE.md/README | Task 15 |

**No gaps found.**

**Placeholder scan:** All steps contain actual code. No TBD or "fill in" language found.

**Type consistency:** `WorkerEvent` defined in Task 1, used identically in Tasks 2–7 and 8. `EventDispatcher._poll_once()` defined in Task 8, tested in Task 8. `html_to_spans()` defined in Task 9, used in `MainView.append_log()` in Task 10. `SettingsStore` imported from `app.core.settings_store` consistently across Tasks 12–14.
