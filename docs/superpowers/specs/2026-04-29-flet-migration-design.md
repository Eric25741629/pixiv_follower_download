# Flet 遷移設計

**日期**：2026-04-29
**分支**：`feature/flet-frontend`
**目標**：將 Pixiv 下載器的 GUI 框架從 PyQt5 全面遷移至 Flet，同時讓 `app/core/` 完全不依賴任何 GUI 框架。

---

## 1. 背景與動機

現有應用程式以 PyQt5 + `test.ui`（Qt Designer 載入）實作。問題：

- `app/core/` 的 worker thread（`pixiv_thread_base.py`、`thread_following/pid_scan/url_fetch/download.py`）使用 `QThread` 與 `pyqtSignal`，把 Qt 耦合進核心層。
- PyQt5 部署較重；想要同時支援桌面與 web 介面。
- 想趁機重新設計 UI 風格。

遷移後：

- `app/core/` 不再 import 任何 GUI 框架。
- `app/gui/` 改用 Flet（Material 3 風格），桌面與 web 模式雙支援。
- 既有的 `cookies.json` / `othersettings.json` 等持久化資料格式完全不變。

---

## 2. 範圍

**包含**

- 改寫 `app/core/` 中所有 `QThread` / `pyqtSignal` 程式碼，改用 `threading.Thread` + `queue.Queue` 事件機制。
- 全新 Flet UI（不複製現有 `test.ui` 版面）。
- 維持現有 4 步驟工作流（追蹤 → PID → URL → 下載）與一鍵執行行為。
- 維持 cookie pool、JXL 轉檔、tag 過濾等所有現有功能。
- 更新測試以對應新介面。

**不包含**

- 不修改 Pixiv API 呼叫邏輯（`app/core/pixiv_api.py`）。
- 不變更持久化檔案 schema。
- 不重寫下載 / JXL 轉換 / tag 過濾的核心邏輯。

---

## 3. 架構

### 3.1 分層

```
app/
├── core/                          # 完全 Qt-free
│   ├── worker_event.py            # 新增：事件 dataclass
│   ├── pixiv_thread_base.py       # 改寫：去 Qt
│   ├── thread_following.py        # 改寫：emit → queue.put
│   ├── thread_pid_scan.py         # 改寫
│   ├── thread_url_fetch.py        # 改寫
│   ├── thread_download.py         # 改寫
│   └── ...（其餘檔案不變）
├── gui/                           # Flet 介面
│   ├── flet_app.py                # 新增：主 Flet page
│   ├── views/                     # 新增：分頁元件
│   │   ├── main_view.py           # 主頁（步驟 + log + 進度）
│   │   ├── settings_view.py       # 設定頁
│   │   └── cookies_view.py        # Cookie 管理頁
│   ├── dispatcher.py              # 新增：queue → UI 分派
│   ├── log_format.py              # 新增：HTML → TextSpan 解析
│   └── user_info.py               # 改寫：去 Qt imports
└── entry/
    └── main.py                    # 改寫：呼叫 flet.app()
```

刪除：

- `app/gui/controller.py`
- `app/gui/run_actions.py`
- `test.ui`
- `uimake.py`
- `trash/Ui2.py`

### 3.2 事件系統

**`app/core/worker_event.py`**

```python
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class WorkerEvent:
    type: str   # "output" | "progress" | "countdown" | "finished" | "next"
    data: Any
```

事件類型對應：

| 舊 signal | 新 event.type | data 型別 |
|---|---|---|
| `_output(str)` | `"output"` | `str`（含 HTML 顏色） |
| `_signal(int, int)` | `"progress"` | `tuple[int, int]`（current, total） |
| `_countdown(int)` | `"countdown"` | `int`（剩餘秒數） |
| `_finished(str)` | `"finished"` | `str`（訊息） |
| `_thenext(int)` | `"next"` | `int`（下一步驟編號，-1=停止） |

### 3.3 Thread 基底類別改寫

**`app/core/pixiv_thread_base.py`**

```python
import threading
import queue
import time
from .worker_event import WorkerEvent

class PixivThreadBase(threading.Thread):
    def __init__(self, q: queue.Queue, *args, **kwargs):
        super().__init__(daemon=True)
        self._q = q
        self._pause_event = threading.Event()
        self._pause_event.set()  # 預設不暫停
        self._stop_event = threading.Event()

    def pause(self):
        self._pause_event.clear()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已暫停</font></p>"))

    def resume(self):
        self._pause_event.set()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已繼續</font></p>"))

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已停止</font></p>"))

    def _wait_with_countdown(self, total_seconds: int):
        for remaining in range(total_seconds, 0, -1):
            if self._stop_event.is_set():
                return
            self._pause_event.wait()
            self._q.put(WorkerEvent("countdown", remaining))
            time.sleep(1)
        self._q.put(WorkerEvent("countdown", 0))
```

各子類別 thread 同樣的轉換規則：建構子接 `q`，所有 `self._xxx.emit(...)` 改成 `self._q.put(WorkerEvent(...))`。

### 3.4 Flet 端事件分派

**`app/gui/dispatcher.py`**

```python
import queue
import time

class EventDispatcher:
    def __init__(self, page, q: queue.Queue, handlers: dict):
        self.page = page
        self.q = q
        self.handlers = handlers   # type -> callable(data)
        self._stop = False

    def run(self):
        while not self._stop:
            updated = False
            try:
                while True:
                    ev = self.q.get_nowait()
                    handler = self.handlers.get(ev.type)
                    if handler:
                        handler(ev.data)
                    updated = True
            except queue.Empty:
                pass
            if updated:
                self.page.update()
            time.sleep(0.05)   # 50ms 輪詢

    def stop(self):
        self._stop = True
```

啟動方式：`page.run_thread(dispatcher.run)`。

---

## 4. UI 設計（全新 Flet 版本）

### 4.1 整體佈局

左側 `NavigationRail`，右側主內容區。`AppBar` 上有深淺色切換。

```
┌──────────────────────────────────────────────────────┐
│ AppBar：Pixiv 下載器  [深淺色]                       │
├──────┬───────────────────────────────────────────────┤
│ 🏠 主頁│                                              │
│ ⚙️ 設定│             主內容（依分頁切換）             │
│ 🍪 Cookie│                                            │
│ ❓ 關於│                                              │
└──────┴───────────────────────────────────────────────┘
```

### 4.2 主頁

- 4 個步驟卡片（`Card`），狀態以顏色顯示：待機灰 / 執行中藍 / 完成綠 / 失敗紅
- 一鍵執行 / 暫停 / 停止 控制按鈕（`FilledButton` / `OutlinedButton`）
- `ProgressBar` + 數字進度（如「45/120」）
- 倒數計時顯示（執行中才顯示）
- Log 區：`ListView` + `Text(spans=[...])`，自動捲到最新；長度上限 2000 行

### 4.3 設定頁

用 `ExpansionTile` 把現有設定分組：

- **帳號設定** — 帳號、密碼、儲存路徑（`FilePicker`）
- **過濾規則** — 讚數門檻（一般 / R18）、規則 1 / 2、隱藏追蹤、過濾 GIF / 無 tag / 無時間
- **標籤過濾** — 必須 tag、禁止 tag（`Chip` 列表 + 新增輸入框）
- **JXL 轉檔** — 啟用、cjxl 路徑（`FilePicker`）、effort、刪除原檔
- **下載設定** — 下載間隔等

### 4.4 Cookie 管理頁

`DataTable` 顯示 cookies，欄位：別名、狀態、最後測試時間、操作。

- 新增 / 編輯：`AlertDialog` 含別名 + cookie 字串輸入
- 測試失效：呼叫既有的測試流程（透過事件 queue）

### 4.5 風格

- Material 3，跟隨系統深淺色（Flet `theme_mode=ThemeMode.SYSTEM`）
- 強調色：Pixiv 藍 `#0096FA`
- Noto Sans CJK fallback 字體
- 預設 padding 8 / 16 / 24
- 使用 Flet 內建 `animate_*` 屬性做頁面切換、按鈕 hover、log 新行淡入

### 4.6 Log 顏色解析

**`app/gui/log_format.py`**

解析現有的 HTML pattern（`<p>`, `<font color='X'>...</font>`）成 Flet `TextSpan` 列表。覆蓋的 case：

- `<font color='red'>x</font>` → `TextSpan("x", style=TextStyle(color=Colors.RED))`
- `<font color='green'>x</font>` → 綠
- `<font color='black'>x</font>` → 預設色
- `<font color='gray'>x</font>` → 灰
- 巢狀 `<p><font ...>...</font></p>` → 一段 paragraph
- 純文字 → 單一 `TextSpan`，預設色

實作：用 `re` 解析（這個 HTML 是工程式產生，pattern 有限），不引入完整 HTML parser。

---

## 5. 測試策略

### 5.1 Core 測試（去 Qt 後）

不再需要 `QApplication`。

- `tests/test_worker_event.py` — dataclass 行為
- `tests/test_thread_base.py` — `pause / resume / stop` 用 `threading.Event` 驗證；事件正確 push 到 queue
- `tests/test_cookie_cooldown.py` — 改用新 queue API；assert queue 中的事件
- `tests/test_jxl_fallback.py` — 同上

### 5.2 Flet 測試

- `tests/test_log_format.py` — HTML → `TextSpan` 解析的 unit test，涵蓋所有顏色與巢狀 case
- `tests/test_dispatcher.py` — 模擬 queue 事件流，驗證 dispatcher 正確呼叫 handler

UI 互動測試靠手動驗收（成本對比效益不划算）。

### 5.3 回歸保證

- `pytest` 全綠
- `ruff check app/`、`radon cc app/ -n C -s`、`lizard -C 15 -L 100 app/`、`vulture` 不退步

---

## 6. 遷移階段

每個階段獨立 commit、獨立通過測試。

### Phase 1：Core 去 Qt

1. 新增 `app/core/worker_event.py`
2. 改寫 `app/core/pixiv_thread_base.py`：`QThread` → `threading.Thread`，移除 `pyqtSignal`
3. 逐個改寫 `thread_following.py` / `thread_pid_scan.py` / `thread_url_fetch.py` / `thread_download.py`：`.emit(...)` → `self._q.put(WorkerEvent(...))`
4. 既有測試改用 queue 介面，必須全綠

**驗收點**：用 CLI 腳本（不啟動 GUI）能完整跑完 4 步驟。

### Phase 2：Flet 骨架

1. 加入 Flet 依賴（`pyproject.toml`）
2. 新增 `app/gui/flet_app.py`（最簡 main page）
3. 新增 `app/gui/dispatcher.py`，啟動 dispatcher loop
4. 改寫 `app/entry/main.py`：呼叫 `flet.app(target=main, ...)`

**驗收點**：Flet 視窗能開啟，dispatcher 印出 queue 事件。

### Phase 3：主頁

1. 步驟卡片、執行控制按鈕
2. `ProgressBar` + 倒數計時
3. Log 區（含 HTML → `TextSpan`）
4. 接通 dispatcher，端到端跑一次下載

**驗收點**：完整跑完 4 步驟，log 顏色正確顯示，進度條正確。

### Phase 4：設定頁

1. `ExpansionTile` 分組
2. 各設定的 Flet 控制項（`TextField` / `Switch` / `Slider` / `FilePicker`）
3. 改寫 `app/gui/user_info.py`：移除 `QFileDialog` / `QDateTime` 依賴
4. 用 `FilePicker` 取代檔案選擇

**驗收點**：所有設定能讀寫 `%APPDATA%/pixiv_download/*.json`，重開能還原。

### Phase 5：Cookie 管理頁

1. `DataTable` 顯示 cookies
2. 新增 / 編輯 `AlertDialog`
3. 測試失效按鈕

**驗收點**：cookie pool 增減、別名、狀態顯示正確。

### Phase 6：清理 + Web 模式驗證

1. 刪除 `app/gui/controller.py`、`run_actions.py`、`test.ui`、`uimake.py`、`trash/Ui2.py`
2. 移除 `pyproject.toml` 中的 PyQt5 / qfluentwidgets 依賴
3. 跑 `flet run --web` 驗證瀏覽器模式
4. 更新 `CLAUDE.md`、`README.md`

**驗收點**：repo 內無 `from PyQt5` / `import PyQt5`；`flet.app()` 桌面 + `flet run --web` 都能跑。

---

## 7. 風險與緩解

| 風險 | 緩解 |
|---|---|
| Phase 1 改動大，core 既有測試可能被破壞 | 每個 thread 改完都跑完整 pytest |
| HTML 顏色解析遺漏邊界 case | `test_log_format.py` 涵蓋常見 pattern |
| Flet 視窗控制與既有 `FramelessMainWindow` 行為差異 | Phase 2 直接用 Flet 內建 `WindowDragArea`，不自己重做 |
| `FilePicker` 在 web 模式下行為不同 | Phase 4 同時驗證桌面 + web |
| 既有 JSON schema 不能變 | Flet 端用既有 `user_info.py` 讀寫邏輯，schema 完全不動 |
| Flet 在 50ms 輪詢下的性能 | 主頁 log 用 `ListView` 虛擬化，控制行數上限 2000 |

---

## 8. 後續工作（不在本 spec 範圍）

- Flet 桌面打包（PyInstaller / `flet pack`）
- Web 模式的多 session 隔離（如果之後要部署成共用服務）
- Flet 主題客製化（Pixiv 品牌色完整套用）
