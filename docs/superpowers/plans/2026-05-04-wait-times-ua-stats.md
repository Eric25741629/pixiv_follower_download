# Wait Times / Chrome UA / Free-Cookie Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make intra-PID wait, free-cookie wait, and User-Agent all configurable from the Settings page; auto-detect installed Chrome version; show free-cookie request count separately in the stats chart.

**Architecture:** Six independent changes applied in order: defaults → new module → wiring fix → UI → stats display → Step 3 summary. Each change is self-contained and committed separately.

**Tech Stack:** Python 3.12, Flet 0.84, winreg (stdlib), pytest

---

## File Map

| File | Change |
|---|---|
| `app/core/settings_store.py` | Add 2 new keys; bump 2 existing defaults |
| `app/core/chrome_detect.py` | **New file** — Chrome UA detection |
| `app/gui/run_actions.py` | Rewire `intra_pid_wait_min/max` to new keys |
| `app/gui/views/settings_view.py` | Fix Slider layout; add 4 wait TextFields; add UA tile |
| `app/gui/views/stats_view.py` | Separate "免Cookie" from cookie bars in chart |
| `app/core/thread_url_fetch.py` | Add dedicated free-cookie summary emit |
| `tests/test_settings_store.py` | Extend with new-key assertions |
| `tests/test_chrome_detect.py` | **New file** — unit tests with mocked winreg |

---

## Task 1: Add new settings keys

**Files:**
- Modify: `app/core/settings_store.py:42-49`
- Test: `tests/test_settings_store.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_settings_store.py`:

```python
def test_intra_pid_wait_defaults(tmp_path):
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["intra_pid_wait_min"] == 5
    assert perf["intra_pid_wait_max"] == 15

def test_nocookie_wait_defaults_bumped(tmp_path):
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["pid_wait_nocookie_min"] == 3
    assert perf["pid_wait_nocookie_max"] == 8

def test_new_keys_merged_into_old_settings(tmp_path):
    """Existing settings.json without new keys still gets defaults via _merge_defaults."""
    (tmp_path / "settings.json").write_text(
        '{"performance": {"pid_cooldown_avg": 40}}', encoding="utf-8"
    )
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["intra_pid_wait_min"] == 5
    assert perf["intra_pid_wait_max"] == 15
```

- [ ] **Step 2: Run to confirm FAIL**

```
pytest tests/test_settings_store.py::test_intra_pid_wait_defaults -v
```
Expected: `FAILED — KeyError` or `AssertionError`

- [ ] **Step 3: Update DEFAULTS in `app/core/settings_store.py`**

Replace lines 42-49:
```python
    "performance": {
        "single_thread_mode": False,
        "pid_cooldown_avg": 35,
        "pid_wait_min": 10,
        "pid_wait_max": 60,
        "pid_wait_nocookie_min": 3,
        "pid_wait_nocookie_max": 8,
        "intra_pid_wait_min": 5,
        "intra_pid_wait_max": 15,
    },
```

- [ ] **Step 4: Run tests to confirm PASS**

```
pytest tests/test_settings_store.py -v
```
Expected: all pass (including the 3 new tests)

- [ ] **Step 5: Commit**

```
git add app/core/settings_store.py tests/test_settings_store.py
git commit -m "feat: add intra_pid_wait_min/max settings keys; bump nocookie defaults to 3/8"
```

---

## Task 2: Chrome UA detection module

**Files:**
- Create: `app/core/chrome_detect.py`
- Create: `tests/test_chrome_detect.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_chrome_detect.py`:

```python
"""Tests for Chrome UA detection (mocked registry / filesystem)."""
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import():
    from app.core.chrome_detect import detect_chrome_ua, _read_from_registry, _read_from_appdata
    return detect_chrome_ua, _read_from_registry, _read_from_appdata


def test_detect_returns_none_when_no_chrome():
    detect_chrome_ua, _, _ = _import()
    with patch("app.core.chrome_detect._read_from_registry", return_value=None), \
         patch("app.core.chrome_detect._read_from_appdata", return_value=None):
        assert detect_chrome_ua() is None


def test_detect_returns_ua_string_from_registry():
    detect_chrome_ua, _, _ = _import()
    with patch("app.core.chrome_detect._read_from_registry", return_value="124.0.6367.91"), \
         patch("app.core.chrome_detect._read_from_appdata", return_value=None):
        ua = detect_chrome_ua()
    assert ua is not None
    assert "Chrome/124.0.6367.91" in ua
    assert ua.startswith("Mozilla/5.0")


def test_detect_falls_back_to_appdata():
    detect_chrome_ua, _, _ = _import()
    with patch("app.core.chrome_detect._read_from_registry", return_value=None), \
         patch("app.core.chrome_detect._read_from_appdata", return_value="123.0.6312.58"):
        ua = detect_chrome_ua()
    assert ua is not None
    assert "Chrome/123.0.6312.58" in ua


def test_registry_returns_none_when_winreg_missing():
    _, _read_from_registry, _ = _import()
    with patch.dict("sys.modules", {"winreg": None}):
        # ImportError path: should return None, not raise
        result = _read_from_registry()
    assert result is None


def test_appdata_returns_none_when_dir_missing(tmp_path):
    _, _, _read_from_appdata = _import()
    with patch("os.environ.get", return_value=str(tmp_path)):
        result = _read_from_appdata()
    assert result is None


def test_appdata_picks_latest_version(tmp_path):
    _, _, _read_from_appdata = _import()
    chrome_app = tmp_path / "Google" / "Chrome" / "Application"
    chrome_app.mkdir(parents=True)
    (chrome_app / "123.0.6312.58").mkdir()
    (chrome_app / "124.0.6367.91").mkdir()
    (chrome_app / "notaversion").mkdir()
    with patch("os.environ.get", return_value=str(tmp_path)):
        result = _read_from_appdata()
    assert result == "124.0.6367.91"
```

- [ ] **Step 2: Run to confirm FAIL**

```
pytest tests/test_chrome_detect.py -v
```
Expected: `ModuleNotFoundError: app.core.chrome_detect`

- [ ] **Step 3: Create `app/core/chrome_detect.py`**

```python
"""Detect the locally installed Chrome version and build a matching User-Agent string."""
from __future__ import annotations
import os
import re


def detect_chrome_ua() -> str | None:
    """Return a Chrome User-Agent string matching the installed Chrome, or None."""
    version = _read_from_registry() or _read_from_appdata()
    if not version:
        return None
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Safari/537.36"
    )


def _read_from_registry() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    candidates = [
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
    ]
    for hive, path in candidates:
        try:
            with winreg.OpenKey(hive, path) as key:
                val, _ = winreg.QueryValueEx(key, "version")
                if isinstance(val, str) and val.strip():
                    return val.strip()
        except OSError:
            continue
    return None


def _read_from_appdata() -> str | None:
    try:
        local = os.environ.get("LOCALAPPDATA", "")
        base = os.path.join(local, "Google", "Chrome", "Application")
        if not os.path.isdir(base):
            return None
        version_pat = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
        versions = [d for d in os.listdir(base) if version_pat.match(d)]
        if not versions:
            return None
        versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)
        return versions[0]
    except Exception:
        return None
```

- [ ] **Step 4: Run tests to confirm PASS**

```
pytest tests/test_chrome_detect.py -v
```
Expected: all 6 tests pass

- [ ] **Step 5: Commit**

```
git add app/core/chrome_detect.py tests/test_chrome_detect.py
git commit -m "feat: add chrome_detect module for auto-detecting installed Chrome UA"
```

---

## Task 3: Rewire intra_pid_wait in run_actions.py

**Files:**
- Modify: `app/gui/run_actions.py:444-445`

- [ ] **Step 1: Edit `app/gui/run_actions.py`**

Replace lines 444-445:
```python
            intra_pid_wait_min=int(perf.get("intra_pid_wait_min", 5)),
            intra_pid_wait_max=int(perf.get("intra_pid_wait_max", 15)),
```

(was `pid_wait_nocookie_min/max` with defaults 1/6)

- [ ] **Step 2: Run existing tests to confirm nothing broken**

```
pytest tests/test_run_actions_scheduler.py tests/test_download_thread_init_helpers.py -v
```
Expected: all pass

- [ ] **Step 3: Commit**

```
git add app/gui/run_actions.py
git commit -m "fix: wire intra_pid_wait from its own settings keys instead of nocookie keys"
```

---

## Task 4: Settings UI — fix Slider layout + add wait fields + UA tile

**Files:**
- Modify: `app/gui/views/settings_view.py`

This task has no automated tests (Flet GUI). Manual verification at the end.

- [ ] **Step 1: Add new instance variables in `__init__` (after the existing Proxy block, ~line 115)**

Insert after `self._proxy_test_results = ft.Column([], spacing=4)`:

```python
        # Wait time controls
        intra_min = int(perf.get("intra_pid_wait_min", 5))
        intra_max = int(perf.get("intra_pid_wait_max", 15))
        self._tf_intra_min = ft.TextField(
            value=str(intra_min), width=80, keyboard_type=ft.KeyboardType.NUMBER,
        )
        self._tf_intra_max = ft.TextField(
            value=str(intra_max), width=80, keyboard_type=ft.KeyboardType.NUMBER,
        )
        nocookie_min = int(perf.get("pid_wait_nocookie_min", 3))
        nocookie_max = int(perf.get("pid_wait_nocookie_max", 8))
        self._tf_nocookie_min = ft.TextField(
            value=str(nocookie_min), width=80, keyboard_type=ft.KeyboardType.NUMBER,
        )
        self._tf_nocookie_max = ft.TextField(
            value=str(nocookie_max), width=80, keyboard_type=ft.KeyboardType.NUMBER,
        )

        # User-Agent controls
        self._tf_agent = ft.TextField(
            value=auth.get("agent", ""),
            hint_text="未設定，將使用內建隨機 UA",
            expand=True,
        )
        self._btn_detect_ua = ft.OutlinedButton(
            "重新偵測 Chrome", on_click=self._on_detect_chrome,
        )
        self._label_ua_status = ft.Text("", size=11, color=ft.Colors.GREY_600)
```

- [ ] **Step 2: Add `_on_detect_chrome` handler method (after `_on_test_proxies`)**

Insert before the `# Save` comment block:

```python
    def _on_detect_chrome(self, e: ft.ControlEvent) -> None:
        from app.core.chrome_detect import detect_chrome_ua
        ua = detect_chrome_ua()
        if ua:
            self._tf_agent.value = ua
            version = ua.split("Chrome/")[1].split(" ")[0] if "Chrome/" in ua else ua
            self._label_ua_status.value = f"已從登錄檔偵測到 Chrome {version}，UA 已更新"
            self._label_ua_status.color = ft.Colors.GREEN_600
        else:
            self._label_ua_status.value = "找不到 Chrome 安裝（已檢查登錄檔與 AppData），請手動填寫 UA"
            self._label_ua_status.color = ft.Colors.RED_600
        try:
            self._tf_agent.update()
            self._label_ua_status.update()
        except Exception:
            pass
```

- [ ] **Step 3: Update `save()` — performance section (lines 291-296)**

Replace:
```python
            "performance": {
                "single_thread_mode": self._sw_single_thread.value,
                "pid_cooldown_avg": self._safe_int_cooldown(),
                "pid_wait_nocookie_min": int(store.get_section("performance").get("pid_wait_nocookie_min", 1)),
                "pid_wait_nocookie_max": int(store.get_section("performance").get("pid_wait_nocookie_max", 6)),
            },
```

With:
```python
            "performance": {
                **store.get_section("performance"),
                "single_thread_mode": self._sw_single_thread.value,
                "pid_cooldown_avg": self._safe_int_cooldown(),
                "pid_wait_nocookie_min": self._clamp_wait(self._tf_nocookie_min, 3, lo=1),
                "pid_wait_nocookie_max": self._clamp_wait_max(self._tf_nocookie_min, self._tf_nocookie_max, 3, 8),
                "intra_pid_wait_min": self._clamp_wait(self._tf_intra_min, 5, lo=1),
                "intra_pid_wait_max": self._clamp_wait_max(self._tf_intra_min, self._tf_intra_max, 5, 15),
            },
```

- [ ] **Step 4: Update `save()` — auth section (lines 260-266)**

Replace:
```python
        store.update_section("auth", {
            **auth_existing,
            "account": self._tf_account.value,
            "password": self._tf_password.value,
            "userid": self._tf_userid.value,
            "proxy_pool": parse_proxy_list(self._tf_proxy_pool.value or ""),
        })
```

With:
```python
        store.update_section("auth", {
            **auth_existing,
            "account": self._tf_account.value,
            "password": self._tf_password.value,
            "userid": self._tf_userid.value,
            "proxy_pool": parse_proxy_list(self._tf_proxy_pool.value or ""),
            "agent": self._tf_agent.value.strip(),
        })
```

- [ ] **Step 5: Add `_clamp_wait` and `_clamp_wait_max` helper methods (after `_safe_int_cooldown`)**

```python
    @staticmethod
    def _clamp_wait(tf: ft.TextField, default: int, lo: int = 1) -> int:
        try:
            return max(lo, int(tf.value or str(default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp_wait_max(tf_min: ft.TextField, tf_max: ft.TextField, default_min: int, default_max: int) -> int:
        try:
            lo = max(1, int(tf_min.value or str(default_min)))
            hi = int(tf_max.value or str(default_max))
            return max(lo, hi)
        except (TypeError, ValueError):
            return default_max
```

- [ ] **Step 6: Update `build()` — fix Slider layout and add new rows (lines 413-417)**

Replace:
```python
                _tile("冷卻設定", [
                    ft.Row([self._tf_cooldown, self._sl_cooldown], spacing=12),
                    self._label_cooldown_hint,
                    self._sw_single_thread,
                ]),
```

With:
```python
                _tile("冷卻設定", [
                    ft.Row([self._tf_cooldown, self._label_cooldown_hint], spacing=12),
                    self._sl_cooldown,
                    ft.Row(
                        [ft.Text("同 PID 頁間等待（秒）", size=13),
                         self._tf_intra_min, ft.Text("~"), self._tf_intra_max],
                        spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [ft.Text("免 Cookie 請求等待（秒）", size=13),
                         self._tf_nocookie_min, ft.Text("~"), self._tf_nocookie_max],
                        spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._sw_single_thread,
                ]),
```

- [ ] **Step 7: Add UA tile in `build()` — insert after Proxy 設定 tile (before the save_btn Container)**

Replace:
```python
                ft.Container(content=save_btn, padding=ft.padding.only(top=8)),
```

With:
```python
                _tile("User-Agent 設定", [
                    ft.Row([self._tf_agent, self._btn_detect_ua], spacing=8),
                    self._label_ua_status,
                ]),
                ft.Container(content=save_btn, padding=ft.padding.only(top=8)),
```

- [ ] **Step 8: Manual smoke test**

```
python main.py
```

Open Settings page. Verify:
- 「冷卻設定」展開後 Slider 獨佔一行，hint text 不再重疊
- 「同 PID 頁間等待」和「免 Cookie 請求等待」兩組 min/max 欄位顯示正確預設值
- 「User-Agent 設定」tile 存在，TextField 顯示目前 UA 或空白
- 按「重新偵測 Chrome」顯示成功/失敗訊息
- 儲存後重開 app，值維持不變

- [ ] **Step 9: Commit**

```
git add app/gui/views/settings_view.py
git commit -m "feat(settings): fix cooldown slider overlap; add intra/nocookie wait fields; add Chrome UA tile"
```

---

## Task 5: Separate "免Cookie" in stats chart

**Files:**
- Modify: `app/gui/views/stats_view.py:200-239`

- [ ] **Step 1: Replace `_update_chart` method**

Replace the entire `_update_chart` method (lines 200-239):

```python
    def _update_chart(self, requests: dict[str, int]) -> None:
        if not requests:
            self._chart_container.controls = [ft.Text("尚無資料", size=12, color=ft.Colors.GREY_500)]
            try:
                self._chart_container.update()
            except Exception:
                pass
            return

        free_count = requests.get("免Cookie", 0)
        cookie_requests = {k: v for k, v in requests.items() if k != "免Cookie"}

        rows: list[ft.Control] = []
        if cookie_requests:
            sorted_items = sorted(cookie_requests.items(), key=lambda kv: kv[1], reverse=True)
            max_count = sorted_items[0][1] if sorted_items else 1
            if max_count <= 0:
                max_count = 1
            for idx, (label, count) in enumerate(sorted_items):
                bar_w = max(2, int(count / max_count * _MAX_BAR_PX))
                color = _BAR_COLORS[idx % len(_BAR_COLORS)]
                rows.append(
                    ft.Row(
                        controls=[
                            ft.Text(label, size=11, width=120, overflow=ft.TextOverflow.ELLIPSIS, no_wrap=True),
                            ft.Container(bgcolor=color, width=bar_w, height=18, border_radius=4),
                            ft.Text(str(count), size=11, width=60),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    )
                )

        if free_count > 0:
            rows.append(
                ft.Text(f"免 Cookie：{free_count} 次", size=12, color=ft.Colors.TEAL_700)
            )

        if not rows:
            rows = [ft.Text("尚無資料", size=12, color=ft.Colors.GREY_500)]

        self._chart_container.controls = rows
        try:
            self._chart_container.update()
        except Exception:
            pass
```

- [ ] **Step 2: Run stats tests**

```
pytest tests/test_stats_collector.py -v
```
Expected: all pass (no changes to StatsCollector itself)

- [ ] **Step 3: Commit**

```
git add app/gui/views/stats_view.py
git commit -m "feat(stats): separate 免Cookie count from cookie bar chart"
```

---

## Task 6: Step 3 summary — dedicated free-cookie line

**Files:**
- Modify: `app/core/thread_url_fetch.py` — `_emit_step3_summaries` method (~line 1727)

- [ ] **Step 1: Extend `_emit_step3_summaries`**

Replace:
```python
    def _emit_step3_summaries(self):
        """Run the trailing summary emitters in the order the user expects."""
        self._flush_revoked_pid_file()
        self._emit_step3_filter_skip_final_summary()
        self._emit_step3_query_final_summary(final=True)
        self._emit_cookie_usage_summary("step3", "Step3 Cookie統計")
```

With:
```python
    def _emit_step3_summaries(self):
        """Run the trailing summary emitters in the order the user expects."""
        self._flush_revoked_pid_file()
        self._emit_step3_filter_skip_final_summary()
        self._emit_step3_query_final_summary(final=True)
        self._emit_cookie_usage_summary("step3", "Step3 Cookie統計")
        free = int(self._step3_cookie_req_counts.get("free", 0))
        if free > 0:
            try:
                self._q.put(WorkerEvent("output",
                    f"<p><font color='teal'>免 Cookie 查詢：{free} 次</font></p>"))
            except Exception:
                pass
```

- [ ] **Step 2: Run Step 3 helper tests**

```
pytest tests/test_step3_finalize_helpers.py tests/test_step3_url_helpers.py -v
```
Expected: all pass

- [ ] **Step 3: Commit**

```
git add app/core/thread_url_fetch.py
git commit -m "feat(step3): emit dedicated free-cookie query count in step 3 summary"
```

---

## Final Verification

- [ ] **Run full test suite**

```
pytest -x -q
```
Expected: all pass, no regressions

- [ ] **Manual end-to-end check**

1. Open Settings → 「冷卻設定」: verify no Slider/text overlap, intra and nocookie fields present
2. Open Settings → 「User-Agent 設定」: press detect, verify SnackBar message
3. Change intra min to 8, max to 20, save, restart app — confirm values persist
4. Run Step 3 with free-access PIDs — confirm "免 Cookie 查詢：X 次" appears in log
5. Open Stats view — confirm "免 Cookie：X 次" appears as teal text below cookie bars
