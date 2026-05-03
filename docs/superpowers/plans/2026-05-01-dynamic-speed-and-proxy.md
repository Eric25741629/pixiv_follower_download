# Dynamic Speed + Per-Account Cooldown + Proxy Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static `pid_wait_min/max` range with a single live-adjustable average cooldown per account, add a round-robin `AccountScheduler` that assigns cookies+proxies per PID, and let users bind each cookie to a proxy URL.

**Architecture:** A new `AccountScheduler` state machine holds each `AccountState(cookie, proxy_url, cooldown_until)`. Worker threads (Steps 2/3/4) call `acquire()` before a PID and `release()` after, driving cooldown. A `proxy_utils` module parses proxy URLs and builds `requests`-compatible dicts. The settings UI gains a single cooldown slider; the cookies UI gains a proxy-binding dropdown per cookie.

**Tech Stack:** Python 3.8+, `requests` (already installed), `requests[socks]` (new dependency), `flet 0.84`, `pytest`, `threading`, `dataclasses`.

**Spec reference:** `docs/superpowers/specs/2026-05-01-dynamic-speed-and-proxy-design.md`

---

## File Map

**New files:**
- `app/core/proxy_utils.py` — proxy URL parsing, `to_requests_proxies()`
- `app/core/account_scheduler.py` — `AccountState` dataclass + `AccountScheduler`
- `tests/test_proxy_utils.py`
- `tests/test_account_scheduler.py`
- `tests/test_speed_settings.py`

**Modified files:**
- `app/core/settings_store.py` — new fields `pid_cooldown_avg`, `proxy_pool`, `cookie_proxy_map`; migration from old `pid_wait_min/max`
- `app/core/pixiv_thread_base.py` — add `scheduler` param + `_acquire_account` / `_release_account` helpers
- `app/core/thread_pid_scan.py` — Step 2: use scheduler instead of random cookie + `cookie_speed_divisor`
- `app/core/thread_url_fetch.py` — Step 3: same
- `app/core/thread_download.py` — Step 4: same; remove `cookie_speed_divisor` / `apply_cookie_pool_speedup`
- `app/gui/run_actions.py` — build `AccountScheduler` from settings, pass to Step 2/3/4 threads
- `app/gui/views/settings_view.py` — cooldown slider + proxy tile
- `app/gui/views/cookies_view.py` — proxy dropdown column + auto-pair button
- `requirements.txt` (or `pyproject.toml`) — add `requests[socks]`

---

## Task 1: proxy_utils module

**Files:**
- Create: `app/core/proxy_utils.py`
- Create: `tests/test_proxy_utils.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_proxy_utils.py
import pytest
from app.core.proxy_utils import parse_proxy_url, to_requests_proxies, parse_proxy_list


def test_parse_http():
    assert parse_proxy_url("http://1.2.3.4:8080") == "http://1.2.3.4:8080"


def test_parse_https():
    assert parse_proxy_url("https://1.2.3.4:443") == "https://1.2.3.4:443"


def test_parse_socks5():
    assert parse_proxy_url("socks5://1.2.3.4:1080") == "socks5://1.2.3.4:1080"


def test_parse_socks5h():
    assert parse_proxy_url("socks5h://1.2.3.4:1080") == "socks5h://1.2.3.4:1080"


def test_parse_with_auth():
    url = "socks5://user:pass@1.2.3.4:1080"
    assert parse_proxy_url(url) == url


def test_parse_empty_returns_none():
    assert parse_proxy_url("") is None
    assert parse_proxy_url("   ") is None


def test_parse_comment_returns_none():
    assert parse_proxy_url("# this is a comment") is None


def test_parse_invalid_scheme_returns_none():
    assert parse_proxy_url("ftp://1.2.3.4:21") is None


def test_parse_no_host_returns_none():
    assert parse_proxy_url("http://") is None


def test_to_requests_proxies_none():
    assert to_requests_proxies(None) is None


def test_to_requests_proxies_empty_string():
    assert to_requests_proxies("") is None


def test_to_requests_proxies_http():
    result = to_requests_proxies("http://1.2.3.4:8080")
    assert result == {"http": "http://1.2.3.4:8080", "https": "http://1.2.3.4:8080"}


def test_to_requests_proxies_socks5():
    result = to_requests_proxies("socks5://host:1080")
    assert result == {"http": "socks5://host:1080", "https": "socks5://host:1080"}


def test_parse_proxy_list_basic():
    text = "http://1.1.1.1:80\nsocks5://2.2.2.2:1080"
    assert parse_proxy_list(text) == ["http://1.1.1.1:80", "socks5://2.2.2.2:1080"]


def test_parse_proxy_list_strips_comments_and_blanks():
    text = "\n# comment\nhttp://1.1.1.1:80\n\n  \n"
    assert parse_proxy_list(text) == ["http://1.1.1.1:80"]


def test_parse_proxy_list_dedupes():
    text = "http://1.1.1.1:80\nhttp://1.1.1.1:80"
    assert parse_proxy_list(text) == ["http://1.1.1.1:80"]


def test_parse_proxy_list_empty():
    assert parse_proxy_list("") == []
```

- [ ] **Step 2: Run tests — verify FAIL**

```bash
pytest tests/test_proxy_utils.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` (module doesn't exist yet).

- [ ] **Step 3: Implement `app/core/proxy_utils.py`**

```python
# app/core/proxy_utils.py
from __future__ import annotations
from urllib.parse import urlparse

_SUPPORTED_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4")


def parse_proxy_url(raw: str) -> str | None:
    """Normalize a single proxy URL string. Returns None for empty or bad input."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s.startswith("#"):
        return None
    try:
        parsed = urlparse(s)
    except Exception:
        return None
    if parsed.scheme not in _SUPPORTED_SCHEMES:
        return None
    if not parsed.hostname:
        return None
    return s


def to_requests_proxies(proxy_url: str | None) -> dict | None:
    """Convert a proxy URL to a requests-compatible ``proxies`` dict.

    Returns ``None`` for direct connection (no proxy).
    """
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def parse_proxy_list(text: str) -> list[str]:
    """Parse a multiline proxy list, stripping blank lines and ``#`` comments.

    Deduplicates while preserving order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parsed = parse_proxy_url(stripped)
        if parsed and parsed not in seen:
            seen.add(parsed)
            result.append(parsed)
    return result
```

- [ ] **Step 4: Run tests — verify PASS**

```bash
pytest tests/test_proxy_utils.py -v
```

Expected: 16 passed.

- [ ] **Step 5: Add `requests[socks]` to dependencies**

Open `requirements.txt` (or `pyproject.toml` `[project.dependencies]`) and add:

```
requests[socks]>=2.31
```

- [ ] **Step 6: Commit**

```bash
git add app/core/proxy_utils.py tests/test_proxy_utils.py requirements.txt
git commit -m "feat(core): proxy_utils — URL parsing and requests proxies dict"
```

---

## Task 2: AccountScheduler — core state machine

**Files:**
- Create: `app/core/account_scheduler.py`
- Create: `tests/test_account_scheduler.py` (partial — basic tests only)

- [ ] **Step 1: Write failing tests for basic acquire/release**

```python
# tests/test_account_scheduler.py
import time
import threading
import pytest
from app.core.account_scheduler import AccountState, AccountScheduler


def _make_scheduler(accounts, avg=60.0):
    pause = threading.Event()
    pause.set()
    stop = threading.Event()
    return AccountScheduler(
        accounts=accounts,
        get_cooldown_avg=lambda: avg,
        pause_event=pause,
        stop_event=stop,
    ), pause, stop


def test_acquire_returns_first_available():
    acc = AccountState(cookie="c1", alias="A1")
    sched, _, _ = _make_scheduler([acc])
    result = sched.acquire()
    assert result is acc


def test_acquire_skips_disabled():
    acc1 = AccountState(cookie="c1", alias="A1")
    acc2 = AccountState(cookie="c2", alias="A2")
    acc1.disabled_reason = "proxy_dead"
    sched, _, _ = _make_scheduler([acc1, acc2])
    result = sched.acquire()
    assert result is acc2


def test_release_sets_cooldown_above_zero():
    acc = AccountState(cookie="c1", alias="A1")
    sched, _, _ = _make_scheduler([acc], avg=60.0)
    sched.release(acc, ok=True)
    assert acc.cooldown_until > time.monotonic()


def test_release_cooldown_within_jitter_range():
    acc = AccountState(cookie="c1", alias="A1")
    before = time.monotonic()
    sched, _, _ = _make_scheduler([acc], avg=10.0)
    sched.release(acc, ok=True)
    low = before + max(1, int(10 * 0.7))
    high = before + int(10 * 1.3) + 1
    assert low <= acc.cooldown_until <= high


def test_release_ok_false_disables_account():
    acc = AccountState(cookie="c1", alias="A1")
    sched, _, _ = _make_scheduler([acc])
    sched.release(acc, ok=False)
    assert acc.disabled_reason == "proxy_dead"


def test_all_disabled_true_when_all_disabled():
    acc1 = AccountState(cookie="c1", alias="A1", disabled_reason="proxy_dead")
    acc2 = AccountState(cookie="c2", alias="A2", disabled_reason="proxy_dead")
    sched, _, _ = _make_scheduler([acc1, acc2])
    assert sched.all_disabled() is True


def test_all_disabled_false_when_one_active():
    acc1 = AccountState(cookie="c1", alias="A1", disabled_reason="proxy_dead")
    acc2 = AccountState(cookie="c2", alias="A2")
    sched, _, _ = _make_scheduler([acc1, acc2])
    assert sched.all_disabled() is False


def test_acquire_returns_none_on_stop():
    acc = AccountState(cookie="c1", alias="A1")
    acc.cooldown_until = time.monotonic() + 9999.0  # far future
    sched, _, stop = _make_scheduler([acc])
    stop.set()
    result = sched.acquire()
    assert result is None


def test_acquire_waits_then_returns_after_cooldown(monkeypatch):
    # Patch time.monotonic to control clock
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda _: None)  # instant

    acc = AccountState(cookie="c1", alias="A1")
    acc.cooldown_until = 5.0
    sched, _, _ = _make_scheduler([acc], avg=10.0)

    # Advance clock past cooldown
    clock[0] = 6.0
    result = sched.acquire()
    assert result is acc
```

- [ ] **Step 2: Run tests — verify FAIL**

```bash
pytest tests/test_account_scheduler.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `app/core/account_scheduler.py`**

```python
# app/core/account_scheduler.py
from __future__ import annotations
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class AccountState:
    cookie: str
    alias: str
    proxy_url: str | None = None
    cooldown_until: float = 0.0          # time.monotonic() timestamp
    disabled_reason: str | None = None   # None = active; 'proxy_dead' = disabled

    @property
    def proxies(self) -> dict | None:
        from app.core.proxy_utils import to_requests_proxies
        return to_requests_proxies(self.proxy_url)


class AccountScheduler:
    """Round-robin per-account cooldown scheduler.

    Single consumer; ``acquire()`` blocks until an account is available or
    the stop event fires. Thread-safe via internal lock.
    """

    def __init__(
        self,
        accounts: list[AccountState],
        get_cooldown_avg: Callable[[], float],
        pause_event: threading.Event,
        stop_event: threading.Event,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self._accounts = list(accounts)
        self._get_cooldown_avg = get_cooldown_avg
        self._pause_event = pause_event
        self._stop_event = stop_event
        self._emit = emit if emit is not None else lambda _: None
        self._lock = threading.Lock()

    # ── public API ─────────────────────────────────────────────────────────

    def acquire(self) -> AccountState | None:
        """Block until an account is ready. Returns None when stop fires."""
        while not self._stop_event.is_set():
            # Honour pause
            while not self._pause_event.is_set():
                if self._stop_event.is_set():
                    return None
                self._pause_event.wait(timeout=0.5)
            if self._stop_event.is_set():
                return None

            with self._lock:
                if self.all_disabled():
                    self._emit(
                        "<p><font color='red'>所有 Cookie 都已禁用，任務停止</font></p>"
                    )
                    return None
                now = time.monotonic()
                available = [
                    a for a in self._accounts
                    if a.disabled_reason is None and a.cooldown_until <= now
                ]
                if available:
                    return available[0]
                earliest = min(
                    a.cooldown_until
                    for a in self._accounts
                    if a.disabled_reason is None
                )
                wait = max(0.0, earliest - now)

            # Poll at most 0.5 s so stop/pause can interrupt
            time.sleep(min(0.5, wait))

        return None

    def release(self, account: AccountState, ok: bool = True) -> None:
        """Record outcome after a PID completes.

        ok=True  → schedule cooldown.
        ok=False → disable account (proxy unreachable).
        """
        with self._lock:
            if not ok:
                account.disabled_reason = "proxy_dead"
                alias = account.alias or account.cookie[:20]
                proxy = account.proxy_url or "本機IP"
                self._emit(
                    f"<p><font color='red'>Cookie「{alias}」proxy「{proxy}」"
                    f"連不通，本輪禁用</font></p>"
                )
                return
            avg = self._get_cooldown_avg()
            low = max(1, int(avg * 0.7))
            high = max(low, int(avg * 1.3))
            cooldown = random.randint(low, high)
            account.cooldown_until = time.monotonic() + cooldown

    def disable(self, account: AccountState, reason: str) -> None:
        with self._lock:
            account.disabled_reason = reason

    def all_disabled(self) -> bool:
        return bool(self._accounts) and all(
            a.disabled_reason is not None for a in self._accounts
        )

    def average_cooldown(self) -> float:
        """Current average cooldown seconds (for UI hint)."""
        return self._get_cooldown_avg()
```

- [ ] **Step 4: Run tests — verify PASS**

```bash
pytest tests/test_account_scheduler.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add app/core/account_scheduler.py app/core/proxy_utils.py tests/test_account_scheduler.py
git commit -m "feat(core): AccountScheduler round-robin per-account cooldown"
```

---

## Task 3: Settings store — new fields + migration

**Files:**
- Modify: `app/core/settings_store.py`
- Modify: `tests/test_settings_store.py`

- [ ] **Step 1: Add failing tests**

Open `tests/test_settings_store.py` and add at the bottom:

```python
# --- new tests for pid_cooldown_avg, proxy_pool, cookie_proxy_map ---

def test_defaults_include_pid_cooldown_avg(tmp_path):
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert "pid_cooldown_avg" in perf
    assert perf["pid_cooldown_avg"] == 35


def test_defaults_include_proxy_pool(tmp_path):
    store = SettingsStore(str(tmp_path))
    auth = store.get_section("auth")
    assert "proxy_pool" in auth
    assert auth["proxy_pool"] == []


def test_defaults_include_cookie_proxy_map(tmp_path):
    store = SettingsStore(str(tmp_path))
    auth = store.get_section("auth")
    assert "cookie_proxy_map" in auth
    assert auth["cookie_proxy_map"] == {}


def test_migration_derives_avg_from_min_max(tmp_path):
    import json
    # Write a legacy settings.json with pid_wait_min/max but no pid_cooldown_avg
    data = {
        "performance": {
            "single_thread_mode": False,
            "pid_wait_min": 20,
            "pid_wait_max": 80,
        },
        "auth": {},
    }
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data))
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    # avg of 20+80 = 50
    assert perf["pid_cooldown_avg"] == 50


def test_round_trip_pid_cooldown_avg(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update_fields("performance", {"pid_cooldown_avg": 45})
    perf = store.get_section("performance")
    assert perf["pid_cooldown_avg"] == 45


def test_round_trip_proxy_pool(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update_fields("auth", {"proxy_pool": ["http://1.1.1.1:80"]})
    auth = store.get_section("auth")
    assert auth["proxy_pool"] == ["http://1.1.1.1:80"]


def test_round_trip_cookie_proxy_map(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update_fields("auth", {"cookie_proxy_map": {"abc": "socks5://x:1080"}})
    auth = store.get_section("auth")
    assert auth["cookie_proxy_map"] == {"abc": "socks5://x:1080"}
```

- [ ] **Step 2: Run tests — verify FAIL**

```bash
pytest tests/test_settings_store.py -v -k "pid_cooldown or proxy_pool or cookie_proxy_map or migration_derives"
```

Expected: several AssertionError / KeyError.

- [ ] **Step 3: Update `app/core/settings_store.py`**

In `DEFAULTS`, update the `"performance"` section:

```python
"performance": {
    "single_thread_mode": False,
    "pid_cooldown_avg": 35,          # seconds; replaces pid_wait_min/max in UI
    "pid_wait_nocookie_min": 1,
    "pid_wait_nocookie_max": 6,
},
```

In `DEFAULTS`, update the `"auth"` section (add two new keys at end):

```python
"auth": {
    "login_mode": 0,
    "agent": "",
    "userid": "",
    "account": "",
    "password": "",
    "cookies": "",
    "cookies_pool": [],
    "cookies_aliases": {},
    "cookies_entries": [],
    "proxy_pool": [],
    "cookie_proxy_map": {},
},
```

In `_merge_defaults`, after the existing merge loop, add migration:

```python
def _merge_defaults(self, raw):
    merged = copy.deepcopy(DEFAULTS)
    for section_key, default_section in DEFAULTS.items():
        raw_section = raw.get(section_key)
        if isinstance(raw_section, dict):
            merged[section_key] = {**default_section, **raw_section}
    # Migrate: derive pid_cooldown_avg from old pid_wait_min/max if absent
    perf = merged.get("performance", {})
    if "pid_cooldown_avg" not in perf or perf.get("pid_cooldown_avg") == 35:
        raw_perf = raw.get("performance", {})
        if "pid_wait_min" in raw_perf or "pid_wait_max" in raw_perf:
            old_min = int(raw_perf.get("pid_wait_min", 10))
            old_max = int(raw_perf.get("pid_wait_max", 60))
            avg = max(5, min(300, (old_min + old_max) // 2))
            perf["pid_cooldown_avg"] = avg
            merged["performance"] = perf
    return merged
```

- [ ] **Step 4: Run tests — verify PASS**

```bash
pytest tests/test_settings_store.py -v
```

Expected: all pass (new + existing).

- [ ] **Step 5: Commit**

```bash
git add app/core/settings_store.py tests/test_settings_store.py
git commit -m "feat(settings): add pid_cooldown_avg, proxy_pool, cookie_proxy_map; migrate from min/max"
```

---

## Task 4: PauseableThread — scheduler injection helpers

**Files:**
- Modify: `app/core/pixiv_thread_base.py`

No new tests needed — existing `tests/test_thread_base.py` covers the base; we only add helper methods.

- [ ] **Step 1: Add `scheduler` param and helpers to `PauseableThread`**

In `app/core/pixiv_thread_base.py`, change `__init__` and add two methods:

```python
class PauseableThread(threading.Thread):
    """Base class: pause/resume/stop with countdown support via queue.Queue."""

    def __init__(self, q: _queue.Queue, scheduler=None):
        super().__init__(daemon=True)
        self._q = q
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()
        self._scheduler = scheduler  # AccountScheduler | None

    # ... existing pause/resume/stop/_on_pause_hook/_on_stop_hook/_sleep_with_countdown ...

    def _acquire_account(self):
        """Acquire the next available account from the scheduler.

        Returns AccountState or None (stop). Falls back to None if no scheduler.
        """
        if self._scheduler is None:
            return None
        return self._scheduler.acquire()

    def _release_account(self, account, ok: bool = True) -> None:
        """Release account back to scheduler after a PID completes."""
        if self._scheduler is None or account is None:
            return
        self._scheduler.release(account, ok=ok)
```

- [ ] **Step 2: Run existing thread base tests**

```bash
pytest tests/test_thread_base.py -v
```

Expected: all pass (no regressions).

- [ ] **Step 3: Commit**

```bash
git add app/core/pixiv_thread_base.py
git commit -m "feat(core): PauseableThread accepts optional AccountScheduler"
```

---

## Task 5: pixiv_api — session injection

**Files:**
- Modify: `app/core/pixiv_api.py`

Goal: Add `make_session(proxy_url)` helper. The three most-called functions
(`Pixiv_info`, `get_pixiv_cookie_requirement`, `ugoira_meta`) gain an optional `session=` keyword arg. Callers that don't pass a session get the existing bare-`requests.get` behaviour (no change).

- [ ] **Step 1: Add `make_session` and wire into the three functions**

Near the top of `app/core/pixiv_api.py`, after the imports, add:

```python
from app.core.proxy_utils import to_requests_proxies

def make_session(proxy_url: str | None = None) -> requests.Session:
    """Create a requests.Session pre-configured with proxy and SSL settings."""
    sess = requests.Session()
    proxies = to_requests_proxies(proxy_url)
    if proxies:
        sess.proxies.update(proxies)
    sess.verify = False
    return sess
```

Find the function `Pixiv_info(url, agent, cookie=None)` (around line 420–450 in the file).
Change its signature to:

```python
def Pixiv_info(url, agent, cookie=None, *, session: requests.Session | None = None):
```

Then replace every `requests.get(url, headers=headers, timeout=(10, 30))` inside `Pixiv_info` with:

```python
_req = session if session is not None else requests
_req.get(url, headers=headers, timeout=(10, 30))
```

Do the same for `get_pixiv_cookie_requirement` (add `session=None` kwarg, replace inner `requests.get` with `(session or requests).get`).

Do the same for `ugoira_meta` (add `session=None` kwarg, replace inner `requests.get`).

- [ ] **Step 2: Verify no regressions**

```bash
pytest tests/test_pixiv_api_cookie_requirement.py tests/test_ugoira_meta_retry.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add app/core/pixiv_api.py
git commit -m "feat(api): make_session helper + optional session= in Pixiv_info / ugoira_meta"
```

---

## Task 6: RunController — build_scheduler helper

**Files:**
- Modify: `app/gui/run_actions.py`

This task adds the function that constructs an `AccountScheduler` from settings,
and changes the `_build_thread` calls for Steps 2, 3, 4 to pass a scheduler.

- [ ] **Step 1: Add `_build_scheduler` and update `_build_thread`**

At the top of `app/gui/run_actions.py`, add imports:

```python
from app.core.account_scheduler import AccountState, AccountScheduler
from app.core.proxy_utils import parse_proxy_url
```

Add the helper method to `RunController`:

```python
def _build_scheduler(self, auth: dict, perf: dict, pause_event, stop_event) -> AccountScheduler:
    """Build an AccountScheduler from settings auth+performance sections."""
    entries = auth.get("cookies_entries") or []
    pool = auth.get("cookies_pool") or []
    alias_map = auth.get("cookies_aliases") or {}
    proxy_map = auth.get("cookie_proxy_map") or {}

    # Build cookie list (prefer entries)
    if isinstance(entries, list) and entries:
        cookies_list = [
            e.get("cookie", "") if isinstance(e, dict) else str(e)
            for e in entries
        ]
    elif isinstance(pool, list) and pool:
        cookies_list = [str(c) for c in pool]
    else:
        raw = str(auth.get("cookies", "") or "")
        cookies_list = [raw] if raw.strip() else []

    accounts = []
    for i, cookie in enumerate(cookies_list):
        cookie = cookie.strip()
        if not cookie:
            continue
        alias = alias_map.get(cookie) or f"Cookie {i + 1}"
        proxy_raw = proxy_map.get(cookie)
        proxy_url = parse_proxy_url(proxy_raw) if proxy_raw else None
        accounts.append(AccountState(cookie=cookie, alias=alias, proxy_url=proxy_url))

    avg = float(perf.get("pid_cooldown_avg", 35))

    return AccountScheduler(
        accounts=accounts,
        get_cooldown_avg=lambda: float(
            _store().get_section("performance").get("pid_cooldown_avg", 35)
        ),
        pause_event=pause_event,
        stop_event=stop_event,
        emit=self._log,
    )
```

In `_build_thread`, change the Step 2 block:

```python
if n == 2:
    authors = _load_author_list()
    if not authors:
        self._log("<p><font color='red'>找不到 following 清單，請先執行步驟 1</font></p>")
        return None
    t = thread_pid_scan.get_pixiv_author_imgID_Thread(
        self._event_q,
        authors,
        agent,
        path,
        cookies,
        load_exist_pid_set(path),
        bool(perf.get("single_thread_mode", False)),
        int(perf.get("pid_cooldown_avg", 35)),
    )
    scheduler = self._build_scheduler(
        auth, perf,
        t._pause_event,
        t._stop_event,
    )
    t._scheduler = scheduler
    return t
```

Change the Step 3 block:

```python
if n == 3:
    authors = _load_author_list()
    t = thread_url_fetch.get_img_url_thread(
        q=self._event_q,
        Author_list=authors,
        Agent=agent,
        cookies=cookies,
        exist_pid=load_exist_pid_set(path),
        ban_tag=list(dl.get("ban_tag", [])),
        must_tag=list(dl.get("must_tag", [])),
        like_num=int(dl.get("like_num", 0)),
        no_to_check=[],
        base_path=path,
        single_thread_mode=bool(perf.get("single_thread_mode", False)),
        pid_cooldown_avg=int(perf.get("pid_cooldown_avg", 35)),
        pid_wait_nocookie_min=int(perf.get("pid_wait_nocookie_min", 1)),
        pid_wait_nocookie_max=int(perf.get("pid_wait_nocookie_max", 6)),
        special_like_rules=[],
    )
    scheduler = self._build_scheduler(
        auth, perf,
        t._pause_event,
        t._stop_event,
    )
    t._scheduler = scheduler
    return t
```

Change the Step 4 block:

```python
if n == 4:
    dl_path = str(dl.get("path", "")).strip()
    if not dl_path:
        self._log("<p><font color='red'>請先在「設定」指定下載路徑</font></p>")
        return None
    dt_str = str(dl.get("download_time", "")).strip()
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S") if dt_str else datetime(1970, 1, 1)
    except Exception:
        dt = datetime(1970, 1, 1)
    t = thread_download.download_thread(
        q=self._event_q,
        nogif=bool(flt.get("nogif", False)),
        notag=bool(flt.get("notag", False)),
        notime=bool(flt.get("notime", False)),
        create_dir=bool(directory.get("create_dir", False)),
        download_path=dl_path,
        cookies=cookies,
        agent=agent,
        download_time=dt,
        no_R18G_dir=bool(directory.get("no_R18G_dir", False)),
        single_thread_mode=bool(perf.get("single_thread_mode", False)),
        download_wait_min=int(perf.get("pid_cooldown_avg", 35)),
        download_wait_max=int(perf.get("pid_cooldown_avg", 35)),
        intra_pid_wait_min=int(perf.get("pid_wait_nocookie_min", 1)),
        intra_pid_wait_max=int(perf.get("pid_wait_nocookie_max", 6)),
        jxl_enable=bool(jxl.get("enable", False)),
        jxl_cjxl_path=str(jxl.get("cjxl_path", "")),
        jxl_delete_original=bool(jxl.get("delete_original", False)),
        jxl_effort=int(jxl.get("effort", 7)),
        like_num=int(dl.get("like_num", 0)),
        ban_tag=list(dl.get("ban_tag", [])),
        must_tag=list(dl.get("must_tag", [])),
        special_like_rules=[],
        ai_gen_dir=bool(directory.get("ai_gen_dir", False)),
    )
    scheduler = self._build_scheduler(
        auth, perf,
        t._pause_event,
        t._stop_event,
    )
    t._scheduler = scheduler
    return t
```

- [ ] **Step 2: Run smoke test — app starts without crash**

```bash
python main.py --help 2>&1 | head -5
# or just import test:
python -c "from app.gui.run_actions import RunController; print('ok')"
```

Expected: `ok` (no import error).

- [ ] **Step 3: Commit**

```bash
git add app/gui/run_actions.py
git commit -m "feat(run): build AccountScheduler from settings and inject into Step 2/3/4 threads"
```

---

## Task 7: thread_pid_scan — wire scheduler (Step 2)

**Files:**
- Modify: `app/core/thread_pid_scan.py`

Replace the `random.choice(cookie_pool)` + `apply_cookie_pool_speedup` pattern
with `self._acquire_account()` / `self._release_account()`.

- [ ] **Step 1: Change constructor signature**

Find `def __init__(self, q, Author_list, Agent, path, cookies, exist_pid, single_thread_mode=False, pid_wait_min=10, pid_wait_max=60):` and change to:

```python
def __init__(
    self,
    q,
    Author_list,
    Agent,
    path,
    cookies,
    exist_pid,
    single_thread_mode=False,
    pid_cooldown_avg: int = 35,
):
    super().__init__(q)
    # ... keep all existing self.xxx = xxx lines ...
    self.pid_cooldown_avg = max(5, int(pid_cooldown_avg))
    # Remove: self.pid_wait_min, self.pid_wait_max setup
```

- [ ] **Step 2: Replace cookie selection + delay logic**

Find the loop in `run()` where each author is processed. The current pattern is:

```python
raw_delay = pyrandom.randint(self.pid_wait_min, self.pid_wait_max)
delay = apply_cookie_pool_speedup(raw_delay, self.cookie_pool)
```

and the `_select_cookie` call uses `pyrandom.choice(self.cookie_pool)`.

Replace the **entire per-author block** with the scheduler pattern:

```python
# Acquire account (blocks until cooldown expires or stop fires)
acc = self._acquire_account()
if acc is None:
    break  # stop event

pid_cookie = acc.cookie if acc else self.cookies
proxies = acc.proxies if acc else None

try:
    # ... existing HTTP request using pid_cookie and proxies ...
    # Pass proxies to every requests.get call in this block:
    # requests.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
    ok = True
except (requests.exceptions.ProxyError,
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ConnectionError):
    ok = False
finally:
    self._release_account(acc, ok=ok)
```

- [ ] **Step 3: Remove dead imports**

Remove the lines that import `apply_cookie_pool_speedup` and `cookie_speed_divisor`
from `app.core.pixiv_thread_utils` at the top of `thread_pid_scan.py`.

- [ ] **Step 4: Run Step 2 tests**

```bash
pytest tests/test_pid_scan.py tests/test_step2_filter.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/core/thread_pid_scan.py
git commit -m "feat(step2): wire AccountScheduler, remove cookie_speed_divisor"
```

---

## Task 8: thread_url_fetch — wire scheduler (Step 3)

**Files:**
- Modify: `app/core/thread_url_fetch.py`

Same pattern as Task 7 but for the URL-fetch thread.

- [ ] **Step 1: Change constructor — replace `pid_wait_min/max` with `pid_cooldown_avg`**

Find the `__init__` signature for `get_img_url_thread` (or its class constructor).
Replace `pid_wait_min=10, pid_wait_max=60` params with `pid_cooldown_avg: int = 35`.
Remove setup lines for `self.pid_wait_min`, `self.pid_wait_max`.

- [ ] **Step 2: Replace cookie selection + delay in the per-PID loop**

Find every place that calls `pyrandom.choice(self.cookie_pool)` and
`apply_cookie_pool_speedup` / `cookie_speed_divisor`.

Replace with:

```python
acc = self._acquire_account()
if acc is None:
    break
pid_cookie = acc.cookie if acc else self.cookies
proxies = acc.proxies if acc else None

try:
    res = requests.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
    ok = True
except (requests.exceptions.ProxyError,
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ConnectionError):
    ok = False
    res = None
finally:
    self._release_account(acc, ok=ok)
```

- [ ] **Step 3: Remove dead imports** (`apply_cookie_pool_speedup`, `cookie_speed_divisor`)

- [ ] **Step 4: Run Step 3 tests**

```bash
pytest tests/test_step3_url_helpers.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/core/thread_url_fetch.py
git commit -m "feat(step3): wire AccountScheduler, remove cookie_speed_divisor"
```

---

## Task 9: thread_download — wire scheduler (Step 4)

**Files:**
- Modify: `app/core/thread_download.py`

This is the largest change. The key difference: Step 4 has two levels of wait
(`_sleep_between_downloads` = inter-PID and `_sleep_within_pid` = intra-PID).
The scheduler handles inter-PID cooldown; intra-PID stays as-is.

- [ ] **Step 1: Change inter-PID delay**

Find `_sleep_between_downloads` (around line 1095). Currently it calls
`_run_download_countdown` with `download_wait_min/max`. In the new model,
the scheduler handles inter-PID cooldown via `release()`, so **this method
should become a no-op** (the scheduler's cooldown IS the inter-PID wait):

```python
def _sleep_between_downloads(self, pid):
    pass  # cooldown is now handled by AccountScheduler.release()
```

- [ ] **Step 2: Wire acquire/release around each PID**

Find the main download loop in `run()`. The loop processes a list of PIDs.
Before each PID's download:

```python
acc = self._acquire_account()
if acc is None:
    break
pid_cookie = acc.cookie if acc else self._select_cookie_for_pid(pid)
proxies = acc.proxies if acc else None
ok = True
try:
    # ... existing download logic, but pass:
    #   cookie=pid_cookie (instead of self._select_cookie_for_pid(pid))
    #   proxies=proxies to every requests.get call
    pass
except (requests.exceptions.ProxyError,
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ConnectionError):
    ok = False
finally:
    self._release_account(acc, ok=ok)
```

- [ ] **Step 3: Remove `cookie_speed_divisor` and `apply_cookie_pool_speedup` usage**

Find all lines in `thread_download.py` that call or reference `cookie_speed_divisor`
and `apply_cookie_pool_speedup`. Remove them.

Also simplify `_calc_sleep_delay` — the no-cookie half-speed and sqrt(N) logic
no longer apply (scheduler handles timing). The function can return a small fixed
value or be removed entirely if only `_sleep_within_pid` uses it:

```python
def _calc_sleep_delay(self, min_sec, max_sec, pid=None):
    return random.randint(int(min_sec), int(max_sec))
```

- [ ] **Step 4: Remove dead imports**

Remove `apply_cookie_pool_speedup`, `cookie_speed_divisor` from import at top.

- [ ] **Step 5: Run Step 4 tests**

```bash
pytest tests/test_step4_download_helpers.py tests/test_download_artwork_helpers.py tests/test_cookie_cooldown.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/core/thread_download.py
git commit -m "feat(step4): wire AccountScheduler, remove inter-PID sleep, remove cookie_speed_divisor"
```

---

## Task 10: Settings UI — cooldown slider + proxy tile

**Files:**
- Modify: `app/gui/views/settings_view.py`

- [ ] **Step 1: Replace wait min/max fields with cooldown avg slider**

In `__init__`, remove:

```python
self._tf_dl_wait_min = ft.TextField(...)
self._tf_dl_wait_max = ft.TextField(...)
```

Add:

```python
cooldown_avg = int(perf.get("pid_cooldown_avg", 35))
self._sl_cooldown = ft.Slider(
    min=5, max=300, divisions=59,
    value=float(cooldown_avg),
    label="{value}",
    width=220,
    on_change=self._on_cooldown_slider_change,
)
self._tf_cooldown = ft.TextField(
    label="平均冷卻秒數",
    value=str(cooldown_avg),
    width=90,
    keyboard_type=ft.KeyboardType.NUMBER,
    on_change=self._on_cooldown_tf_change,
)
self._label_cooldown_hint = ft.Text(
    self._cooldown_hint(cooldown_avg),
    size=11,
    color=ft.Colors.GREY_600,
)
```

Add the two sync handlers and hint function:

```python
def _cooldown_hint(self, avg: float) -> str:
    avg = max(1, float(avg))
    multiplier = 60.0 / avg
    color_warn = avg < 30
    text = f"相當於倍率 {multiplier:.1f}x；推薦 ≥ 30 秒"
    return text

def _on_cooldown_slider_change(self, e: ft.ControlEvent) -> None:
    val = int(e.control.value)
    self._tf_cooldown.value = str(val)
    self._label_cooldown_hint.value = self._cooldown_hint(val)
    self._label_cooldown_hint.color = (
        ft.Colors.RED_600 if val < 30 else ft.Colors.GREY_600
    )
    self._tf_cooldown.update()
    self._label_cooldown_hint.update()

def _on_cooldown_tf_change(self, e: ft.ControlEvent) -> None:
    try:
        val = max(5, min(300, int(self._tf_cooldown.value or "35")))
    except ValueError:
        return
    self._sl_cooldown.value = float(val)
    self._label_cooldown_hint.value = self._cooldown_hint(val)
    self._label_cooldown_hint.color = (
        ft.Colors.RED_600 if val < 30 else ft.Colors.GREY_600
    )
    self._sl_cooldown.update()
    self._label_cooldown_hint.update()
```

- [ ] **Step 2: Update `save()` method**

Remove `"pid_wait_min"` / `"pid_wait_max"` write. Add:

```python
try:
    avg_val = max(5, int(self._tf_cooldown.value or "35"))
except ValueError:
    avg_val = 35

store.update_multiple({
    ...
    "performance": {
        "single_thread_mode": self._sw_single_thread.value,
        "pid_cooldown_avg": avg_val,
        "pid_wait_nocookie_min": int(...),
        "pid_wait_nocookie_max": int(...),
    },
    ...
})
```

- [ ] **Step 3: Add < 30 sec warning dialog in `_save_and_notify`**

```python
def _save_and_notify(self, e):
    try:
        avg_val = max(5, int(self._tf_cooldown.value or "35"))
    except ValueError:
        avg_val = 35

    if avg_val < 30:
        def _confirm(ev):
            self._page.pop_dialog()
            self.save()
            self._page.show_dialog(ft.SnackBar(ft.Text("設定已儲存"), duration=1500))

        def _cancel(ev):
            self._page.pop_dialog()

        self._page.show_dialog(ft.AlertDialog(
            title=ft.Text("冷卻時間偏短"),
            content=ft.Text(
                f"平均冷卻 {avg_val} 秒低於建議值 30 秒，\n可能被 Pixiv 風控偵測。確定要套用？"
            ),
            actions=[
                ft.TextButton("取消", on_click=_cancel),
                ft.FilledButton("確定套用", on_click=_confirm),
            ],
        ))
        return

    self.save()
    self._page.show_dialog(ft.SnackBar(ft.Text("設定已儲存"), duration=1500))
```

- [ ] **Step 4: Add proxy pool tile**

In `__init__`, add:

```python
proxy_pool = auth.get("proxy_pool") or []
self._tf_proxy_pool = ft.TextField(
    label="Proxy 列表（每行一個）",
    hint_text="# 一行一個 proxy\nhttp://1.2.3.4:8080\nsocks5://user:pass@host:1080",
    value="\n".join(proxy_pool),
    multiline=True,
    min_lines=4,
    max_lines=15,
    expand=True,
)
self._proxy_test_results = ft.Column([], spacing=4)
```

Add test button handler:

```python
def _on_test_proxies(self, e: ft.ControlEvent) -> None:
    from app.core.proxy_utils import parse_proxy_list, test_proxy
    lines = parse_proxy_list(self._tf_proxy_pool.value or "")
    self._proxy_test_results.controls = [ft.Text("測試中...", size=11)]
    self._page.update()

    def _run():
        results = []
        for url in lines:
            ok, msg = test_proxy(url, timeout=10)
            icon = "✓" if ok else "✗"
            color = ft.Colors.GREEN_600 if ok else ft.Colors.RED_600
            results.append(ft.Text(f"{icon} {url} — {msg}", size=11, color=color))
        if not results:
            results = [ft.Text("（無有效 proxy）", size=11, color=ft.Colors.GREY_600)]
        self._proxy_test_results.controls = results
        try:
            self._page.update()
        except Exception:
            pass

    import threading
    threading.Thread(target=_run, daemon=True).start()
```

In `build()`, rename the `「下載設定」` tile to `「冷卻設定」` and replace its content.
Add a new `「Proxy 設定」` tile:

```python
_tile("冷卻設定", [
    ft.Row([self._tf_cooldown, self._sl_cooldown], spacing=12),
    self._label_cooldown_hint,
    self._sw_single_thread,
]),
_tile("Proxy 設定", [
    self._tf_proxy_pool,
    ft.Row([
        ft.OutlinedButton("測試全部 Proxy", on_click=self._on_test_proxies),
    ]),
    self._proxy_test_results,
]),
```

Also update `save()` to save proxy pool:

```python
from app.core.proxy_utils import parse_proxy_list
store.update_fields("auth", {
    **store.get_section("auth"),
    "proxy_pool": parse_proxy_list(self._tf_proxy_pool.value or ""),
})
```

- [ ] **Step 5: Add `test_proxy` to `proxy_utils.py`**

```python
def test_proxy(proxy_url: str | None, timeout: int = 10) -> tuple[bool, str]:
    """Synchronously test a proxy by GET-ing https://www.pixiv.net."""
    import requests
    proxies = to_requests_proxies(proxy_url)
    try:
        resp = requests.get(
            "https://www.pixiv.net",
            proxies=proxies,
            timeout=timeout,
            verify=False,
            allow_redirects=True,
        )
        return True, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)[:80]
```

Re-run proxy utils tests:

```bash
pytest tests/test_proxy_utils.py -v
```

Expected: all pass (new function has no unit test for live call).

- [ ] **Step 6: Launch app and verify slider UI**

```bash
python main.py
```

Open Settings → 冷卻設定 tile. Drag slider; verify hint text updates. Enter 10 → verify red hint + save dialog warning.

- [ ] **Step 7: Commit**

```bash
git add app/gui/views/settings_view.py app/core/proxy_utils.py
git commit -m "feat(ui): cooldown slider with <30s warning, proxy pool tile"
```

---

## Task 11: Cookies UI — proxy dropdown + auto-pair

**Files:**
- Modify: `app/gui/views/cookies_view.py`

- [ ] **Step 1: Load proxy pool into `CookiesView`**

In `_load_entries`, also load proxy pool and proxy map:

```python
def _load_entries(self) -> None:
    store = _store()
    store.migrate_from_legacy()
    auth = store.get_section("auth")
    alias_map = auth.get("cookies_aliases", {})
    if not isinstance(alias_map, dict):
        alias_map = {}
    raw = auth.get("cookies_entries", []) or auth.get("cookies_pool", [])
    self._entries = normalize_cookie_entries(raw, alias_map=alias_map)
    self._agent = str(auth.get("agent") or "").strip() or DEFAULT_AGENT
    self._proxy_pool: list[str] = list(auth.get("proxy_pool") or [])
    self._cookie_proxy_map: dict[str, str | None] = dict(auth.get("cookie_proxy_map") or {})
```

- [ ] **Step 2: Save proxy map alongside cookie entries**

In `_save_entries`, also persist `cookie_proxy_map`:

```python
def _save_entries(self) -> None:
    store = _store()
    auth = store.get_section("auth")
    pool = [x.get("cookie", "") for x in self._entries if x.get("cookie", "").strip()]
    alias_map = {
        x["cookie"]: x.get("alias", "")
        for x in self._entries if x.get("cookie", "").strip()
    }
    store.update_section("auth", {
        **auth,
        "cookies_entries": self._entries,
        "cookies_pool": pool,
        "cookies_aliases": alias_map,
        "cookies": pool[0] if pool else "",
        "cookie_proxy_map": self._cookie_proxy_map,
    })
```

- [ ] **Step 3: Add proxy dropdown column to DataTable**

In `__init__`, change the `DataTable` columns:

```python
self._table = ft.DataTable(
    columns=[
        ft.DataColumn(ft.Text("選取")),
        ft.DataColumn(ft.Text("別名")),
        ft.DataColumn(ft.Text("狀態")),
        ft.DataColumn(ft.Text("Cookie 預覽")),
        ft.DataColumn(ft.Text("Proxy 綁定")),  # new
        ft.DataColumn(ft.Text("操作")),
    ],
    rows=[],
)
```

In `_refresh_table`, add the proxy cell for each row:

```python
cookie = entry.get("cookie", "")
current_proxy = self._cookie_proxy_map.get(cookie, "")

proxy_options = [
    ft.dropdown.Option(key="", text="（本機 IP）"),
] + [
    ft.dropdown.Option(key=p, text=p[:50])
    for p in self._proxy_pool
]

proxy_dd = ft.Dropdown(
    options=proxy_options,
    value=current_proxy if current_proxy in self._proxy_pool else "",
    width=200,
    on_change=lambda e, c=cookie: self._on_proxy_change(c, e.control.value),
)

# Insert as DataCell before the 操作 cell
```

Add the handler:

```python
def _on_proxy_change(self, cookie: str, proxy_url: str) -> None:
    self._cookie_proxy_map[cookie] = proxy_url or None
    self._save_entries()
```

- [ ] **Step 4: Add auto-pair button**

In `build()`, add to the header row:

```python
ft.OutlinedButton(
    "自動配對",
    icon=ft.Icons.AUTO_FIX_HIGH,
    on_click=self._on_auto_pair,
),
```

Add handler:

```python
def _on_auto_pair(self, e: ft.ControlEvent) -> None:
    cookies = [
        entry.get("cookie", "")
        for entry in self._entries
        if entry.get("cookie", "").strip()
    ]
    for i, cookie in enumerate(cookies):
        if i < len(self._proxy_pool):
            self._cookie_proxy_map[cookie] = self._proxy_pool[i]
        else:
            self._cookie_proxy_map[cookie] = None
    self._save_entries()
    self._refresh_table()
    self._page.update()
```

- [ ] **Step 5: Reload proxy pool when settings change**

`CookiesView` currently loads entries once in `__init__`. If the user saves proxy settings and comes back to cookies view, they should see updated proxy options. The simplest fix: call `_load_entries()` each time the cookies tab becomes visible. 

In `app/gui/flet_app.py`, find where `NavigationRail` destination changes are handled, and call `cookies_view._load_entries(); cookies_view._refresh_table()` when the cookies tab is selected. (Look for the `on_change` of `ft.NavigationRail`.)

If flet_app currently handles this as a simple container swap, add a reload hook:

```python
# In the navigation on_change callback:
if selected_index == 2:  # cookies tab index
    cookies_view._load_entries()
    cookies_view._refresh_table()
    page.update()
```

- [ ] **Step 6: Launch app and verify**

```bash
python main.py
```

1. Go to Settings → Proxy 設定; add `http://127.0.0.1:9999`; save.
2. Go to Cookies; verify the Proxy 綁定 dropdown shows the proxy.
3. Select it for a cookie; re-open settings and re-open cookies; verify binding persists.
4. Click 自動配對; verify assignment order.

- [ ] **Step 7: Commit**

```bash
git add app/gui/views/cookies_view.py app/gui/flet_app.py
git commit -m "feat(ui): proxy binding dropdown + auto-pair in cookies view"
```

---

## Task 12: Deprecate cookie_speed_divisor / apply_cookie_pool_speedup

**Files:**
- Modify: `app/core/pixiv_thread_utils.py`

- [ ] **Step 1: Add deprecation comments (do NOT delete functions)**

Find `cookie_speed_divisor` and `apply_cookie_pool_speedup` in
`app/core/pixiv_thread_utils.py` and add a one-line comment above each:

```python
# Deprecated: superseded by AccountScheduler per-account cooldown. Kept for import compat.
def cookie_speed_divisor(cookie_pool):
    ...

# Deprecated: superseded by AccountScheduler per-account cooldown. Kept for import compat.
def apply_cookie_pool_speedup(delay, cookie_pool):
    ...
```

- [ ] **Step 2: Verify no callers remain in app/**

```bash
grep -rn "cookie_speed_divisor\|apply_cookie_pool_speedup" app/ --include="*.py"
```

Expected: only definitions in `pixiv_thread_utils.py` and the deprecated aliases
in `pixiv_thread_base.py` remain. No call sites.

- [ ] **Step 3: Commit**

```bash
git add app/core/pixiv_thread_utils.py
git commit -m "chore: mark cookie_speed_divisor and apply_cookie_pool_speedup as deprecated"
```

---

## Task 13: Manual end-to-end verification

This task has no code changes — just verification steps.

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --ignore=tests/test_live_cookie_requirement.py --ignore=tests/test_proxy_live.py
```

Expected: all pass.

- [ ] **Step 2: Start app and verify cooldown slider**

```bash
python main.py
```

- Go to Settings → 冷卻設定
- Drag slider from 35 → 10 → verify red hint + save confirmation dialog
- Set back to 35 → save → no dialog

- [ ] **Step 3: Verify proxy pool and binding**

- Add two proxy URLs in Proxy 設定 tile (can be fake, like `http://127.0.0.1:9999`)
- Save
- Go to Cookies; verify dropdown shows the proxies
- Bind one cookie to the first proxy
- Click 自動配對; verify second cookie gets second proxy

- [ ] **Step 4: Run Step 2 with a real account (integration smoke test)**

- Ensure at least one valid cookie is set
- Click 步驟 2 (抓 PID)
- Verify log shows `acquire account=...` and cooldown countdown after first author
- Verify no `cookie_speed_divisor` or `sqrt` in log

- [ ] **Step 5: Final commit (if any fixup changes were made)**

```bash
git add -A
git commit -m "fix: manual verification fixups" --allow-empty
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Single cooldown slider (avg seconds) replacing min/max — Task 10
- ✅ Hint showing derived multiplier + recommendation — Task 10
- ✅ < 30s warning dialog — Task 10
- ✅ AccountScheduler round-robin — Task 2
- ✅ Per-PID cooldown triggers in Step 2/3/4 — Tasks 7/8/9
- ✅ Proxy URL parsing (http/socks5 auto-detect) — Task 1
- ✅ Proxy multi-line textbox in settings — Task 10
- ✅ Proxy test-all button — Task 10
- ✅ Cookie ↔ Proxy manual binding dropdown — Task 11
- ✅ Auto-pair button — Task 11
- ✅ No-proxy cookie uses local IP (nil proxy_url passes None proxies) — AccountState.proxies
- ✅ Dead proxy disables cookie for run — AccountScheduler.release(ok=False)
- ✅ All disabled → stop task — AccountScheduler.acquire returns None
- ✅ Step 1 (thread_following) unchanged — not touched
- ✅ Settings store migration from old min/max — Task 3
- ✅ Remove cookie_speed_divisor callers — Tasks 7/8/9 + Task 12
- ✅ live reload of cooldown avg in running thread — get_cooldown_avg lambda in RunController._build_scheduler

**Type consistency:**
- `AccountState.proxies` → `dict | None` ✅ matches `requests.get(..., proxies=proxies)`
- `AccountScheduler.acquire()` → `AccountState | None` ✅ used in Tasks 7/8/9
- `AccountScheduler.release(account, ok=True)` → matches all call sites ✅
- `parse_proxy_url` returns `str | None` ✅ stored in `AccountState.proxy_url`
- `pid_cooldown_avg` is `int` in settings ✅ consistent across settings_store, run_actions, thread constructors
