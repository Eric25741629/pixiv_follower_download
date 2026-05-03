# Network Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On `ProxyError` / `ConnectTimeout` / `ConnectionError` raised inside the four scheduler-aware worker call sites (Steps 2/3/4), retry the same call on the same account up to **5 attempts total** with **60 s** between attempts before falling back to `release(ok=False)`.

**Architecture:** Add a single helper `PauseableThread._run_with_network_retry(work_label, fn) -> (ok, result, last_exc)` and an `_wait_interruptible(seconds) -> bool` helper, both in `app/core/pixiv_thread_base.py`. Replace the four current try/except/finally blocks with a single call to the helper followed by the existing `_release_account(acc, ok=ok)`. Pause-time does not count toward the 60 s budget; stop fires immediately.

**Tech Stack:** Python 3.8, `requests`, `threading.Event`, existing `WorkerEvent` queue protocol, `pytest`.

---

## File Structure

**Create:**
- `tests/test_network_retry.py` — unit tests for the new helper.

**Modify:**
- `app/core/pixiv_thread_base.py` — add constants, `_NETWORK_RETRY_EXCEPTIONS`, `_wait_interruptible`, `_run_with_network_retry`, `_emit_output`.
- `app/core/thread_pid_scan.py` — replace try/except in `_run_step2_with_acquired_cookie` (around line 100-123).
- `app/core/thread_url_fetch.py` — replace try/except in `_run_processing_loop` (around line 1257-1280).
- `app/core/thread_download.py` — replace try/except in `_load_artwork_metadata` scheduler branch (around line 607-630) and the single-thread PID-group loop (around line 1545-1568).

---

## Task 1: Add helpers to PauseableThread

**Files:**
- Modify: `app/core/pixiv_thread_base.py` (add imports, constants, methods)
- Test: `tests/test_network_retry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_network_retry.py`:

```python
import threading
import time
import queue as _queue
from unittest.mock import patch

import pytest
import requests

from app.core.pixiv_thread_base import (
    PauseableThread,
    NETWORK_RETRY_ATTEMPTS,
    NETWORK_RETRY_WAIT_SEC,
)
from app.core.worker_event import WorkerEvent


class _Worker(PauseableThread):
    def run(self):
        pass


def _make_worker():
    q = _queue.Queue()
    w = _Worker(q)
    return w, q


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except _queue.Empty:
            return out


def test_constants_are_5_and_60():
    assert NETWORK_RETRY_ATTEMPTS == 5
    assert NETWORK_RETRY_WAIT_SEC == 60


def test_first_attempt_succeeds():
    w, q = _make_worker()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    ok, result, exc = w._run_with_network_retry("PID 1", fn)

    assert ok is True
    assert result == "ok"
    assert exc is None
    assert calls["n"] == 1
    # No retry log lines on success.
    events = _drain(q)
    assert all(e.kind != "output" or "重試" not in e.data for e in events)


def test_third_attempt_succeeds_after_two_proxy_errors():
    w, q = _make_worker()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ProxyError("dead")
        return "ok"

    with patch.object(PauseableThread, "_wait_interruptible", return_value=True) as wait:
        ok, result, exc = w._run_with_network_retry("PID 9", fn)

    assert ok is True
    assert result == "ok"
    assert calls["n"] == 3
    assert wait.call_count == 2
    wait.assert_called_with(NETWORK_RETRY_WAIT_SEC)
    output_msgs = [e.data for e in _drain(q) if e.kind == "output"]
    yellow = [m for m in output_msgs if "#b58900" in m]
    red = [m for m in output_msgs if "color='red'" in m]
    assert len(yellow) == 2
    assert red == []


def test_all_five_attempts_fail():
    w, q = _make_worker()
    calls = {"n": 0}
    err = requests.exceptions.ConnectionError("dead")

    def fn():
        calls["n"] += 1
        raise err

    with patch.object(PauseableThread, "_wait_interruptible", return_value=True):
        ok, result, exc = w._run_with_network_retry("PID 9", fn)

    assert ok is False
    assert result is None
    assert exc is err
    assert calls["n"] == NETWORK_RETRY_ATTEMPTS
    output_msgs = [e.data for e in _drain(q) if e.kind == "output"]
    yellow = [m for m in output_msgs if "#b58900" in m]
    red = [m for m in output_msgs if "color='red'" in m]
    assert len(yellow) == NETWORK_RETRY_ATTEMPTS - 1
    assert len(red) == 1
    assert "停用此 Cookie" in red[0]


def test_stop_during_wait_returns_immediately():
    w, q = _make_worker()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise requests.exceptions.ProxyError("dead")

    # _wait_interruptible returns False if stop fires.
    with patch.object(PauseableThread, "_wait_interruptible", return_value=False):
        ok, result, exc = w._run_with_network_retry("PID 9", fn)

    assert ok is False
    assert result is None
    assert calls["n"] == 1  # Only the first attempt before wait was aborted.


def test_stop_event_set_before_first_attempt():
    w, q = _make_worker()
    w._stop_event.set()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    ok, result, exc = w._run_with_network_retry("PID 9", fn)
    assert ok is False
    assert result is None
    assert calls["n"] == 0


def test_non_network_exception_propagates():
    w, q = _make_worker()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        w._run_with_network_retry("PID 9", fn)
    assert calls["n"] == 1


def test_wait_interruptible_full_duration_returns_true():
    w, _q = _make_worker()
    # Use a small duration so the test is fast. Patch time.monotonic to fake
    # elapsed time; the loop polls every 0.5s so we monkey-patch _stop_event.wait
    # to return False (timeout) and time.monotonic to advance.
    with patch("app.core.pixiv_thread_base.time.monotonic",
               side_effect=[0.0, 0.5, 1.0, 1.5, 2.0]):
        with patch.object(threading.Event, "wait", return_value=False):
            assert w._wait_interruptible(2) is True


def test_wait_interruptible_stop_fires_returns_false():
    w, _q = _make_worker()
    w._stop_event.set()
    assert w._wait_interruptible(60) is False


def test_wait_interruptible_pause_does_not_consume_budget():
    """Paused time must not count against the 60 s wait budget."""
    w, _q = _make_worker()
    w._pause_event.clear()  # paused

    times = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

    def _now():
        try:
            return next(times)
        except StopIteration:
            w._pause_event.set()  # unpause after some polls
            return 0.0

    # We can't easily simulate a full 60s here without slowing the test;
    # instead, check that _wait_interruptible blocks while paused and
    # doesn't return until pause clears.
    started = threading.Event()
    finished = threading.Event()
    result = {}

    def runner():
        started.set()
        result["v"] = w._wait_interruptible(1)
        finished.set()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    started.wait()
    time.sleep(0.6)
    assert not finished.is_set(), "wait should still be blocked while paused"
    w._pause_event.set()
    finished.wait(timeout=3.0)
    assert finished.is_set()
    assert result["v"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_network_retry.py -v`
Expected: ImportError or AttributeError (helpers not yet defined).

- [ ] **Step 3: Add constants and helpers to PauseableThread**

Edit `app/core/pixiv_thread_base.py`. Add at the top of the file (after the existing imports on line 1-11):

```python
import requests
```

Add at module level (between line 17 and class `PauseableThread`):

```python
NETWORK_RETRY_ATTEMPTS = 5
NETWORK_RETRY_WAIT_SEC = 60
_NETWORK_RETRY_EXCEPTIONS = (
    requests.exceptions.ProxyError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
)
```

Inside class `PauseableThread`, add after `_release_account` (after current line 159):

```python
    def _emit_output(self, html: str) -> None:
        try:
            self._q.put(WorkerEvent("output", html))
        except Exception:
            pass

    def _wait_interruptible(self, seconds: float) -> bool:
        """Sleep for `seconds`, polling stop/pause every 0.5 s.

        Returns True if the full duration elapsed, False if stop fired.
        While paused, the wait clock pauses (paused time does not count
        toward the budget — when resumed, the remaining wait continues).
        """
        if seconds <= 0:
            return not self._stop_event.is_set()
        elapsed = 0.0
        # Poll in 0.5 s slices. Paused time is excluded by skipping the
        # tick instead of advancing `elapsed`.
        while elapsed < seconds:
            if self._stop_event.is_set():
                return False
            if not self._pause_event.is_set():
                # Paused — block briefly without advancing elapsed.
                self._pause_event.wait(timeout=0.5)
                continue
            slice_s = min(0.5, seconds - elapsed)
            # _stop_event.wait returns True if set, lets us break instantly.
            if self._stop_event.wait(timeout=slice_s):
                return False
            elapsed += slice_s
        return True

    def _run_with_network_retry(self, work_label: str, fn):
        """Run `fn()` with up to NETWORK_RETRY_ATTEMPTS attempts on the
        scheduler network triple. Returns (ok, result, last_exc).

        - Success: (True, result, None) — caller should release(ok=True).
        - Exhaustion or stop during wait: (False, None, last_exc) — caller
          should release(ok=False) (which keeps current disable-on-failure
          semantics for exhausted retries).
        - Non-network exception: propagates unchanged.

        `work_label` is included in log lines (e.g. "畫師 12345" or
        "PID 67890").
        """
        if self._stop_event.is_set():
            return False, None, None
        last_exc = None
        for attempt in range(1, NETWORK_RETRY_ATTEMPTS + 1):
            try:
                return True, fn(), None
            except _NETWORK_RETRY_EXCEPTIONS as err:
                last_exc = err
                if attempt < NETWORK_RETRY_ATTEMPTS:
                    self._emit_output(
                        f"<p><font color='#b58900'>{work_label} 第 {attempt}/"
                        f"{NETWORK_RETRY_ATTEMPTS} 次失敗"
                        f"（{err.__class__.__name__}），"
                        f"{NETWORK_RETRY_WAIT_SEC} 秒後重試</font></p>"
                    )
                    if not self._wait_interruptible(NETWORK_RETRY_WAIT_SEC):
                        return False, None, last_exc
                else:
                    self._emit_output(
                        f"<p><font color='red'>{work_label} 重試 "
                        f"{NETWORK_RETRY_ATTEMPTS} 次仍失敗"
                        f"（{err.__class__.__name__}），停用此 Cookie</font></p>"
                    )
        return False, None, last_exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_network_retry.py -v`
Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/core/pixiv_thread_base.py tests/test_network_retry.py
git commit -m "feat(retry): add network-retry helper to PauseableThread"
```

---

## Task 2: Wire retry into Step 2 (thread_pid_scan)

**Files:**
- Modify: `app/core/thread_pid_scan.py:94-123`
- Test: `tests/test_account_scheduler.py` and existing step-2 tests must still pass.

- [ ] **Step 1: Replace `_run_step2_with_acquired_cookie` body**

In `app/core/thread_pid_scan.py`, find `_run_step2_with_acquired_cookie` (currently around line 94-123). Replace its body **between the `acc = self._acquire_account()` block and the function's `return result`** with:

```python
    def _run_step2_with_acquired_cookie(self, aid):
        """Single-thread path: acquire from AccountScheduler, run with
        retry, release.

        Returns the PID list on success, None if scheduler returned None
        (stop signal) or the request failed at the proxy level after all
        retries.
        """
        acc = self._acquire_account()
        if acc is None:
            return None  # stop signal or no accounts
        self._record_step2_cookie_usage(aid, acc.cookie)
        proxies = acc.proxies
        ok, result, _ = self._run_with_network_retry(
            f"畫師 {aid}",
            lambda: self.thread_no_use_seleium_get_pid(
                acc.cookie, self.Agent, self.path, '1', aid, proxies=proxies,
            ),
        )
        self._release_account(acc, ok=ok)
        return result
```

This removes the local try/except/finally and the now-redundant single-line "因 proxy 失敗略過" log (the helper logs both yellow retry lines and the final red disable line). `result` is `None` on retry exhaustion, matching the previous `result = None` initialization.

- [ ] **Step 2: Run step-2 tests**

Run: `pytest tests/ -k step2 -v` and `pytest tests/test_account_scheduler.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add app/core/thread_pid_scan.py
git commit -m "feat(retry): wire network retry into Step 2 (thread_pid_scan)"
```

---

## Task 3: Wire retry into Step 3 (thread_url_fetch `_run_processing_loop`)

**Files:**
- Modify: `app/core/thread_url_fetch.py:1257-1280`

- [ ] **Step 1: Replace the try/except/finally block**

In `app/core/thread_url_fetch.py`, find the block in `_run_processing_loop` that currently reads (around line 1257-1280):

```python
            if self._scheduler is not None:
                acc = self._acquire_account()
                if acc is None:
                    break  # stop signal or all disabled
                session = pixiv_api.make_session(acc.proxy_url)
                ok = True
                one = None
                try:
                    one = self.get_download_url(
                        self.path, self.Agent, 1, pid,
                        cookie_override=acc.cookie, session=session,
                    )
                except (requests.exceptions.ProxyError,
                        requests.exceptions.ConnectTimeout,
                        requests.exceptions.ConnectionError) as err:
                    ok = False
                    try:
                        self._q.put(WorkerEvent("output",
                            f"<p><font color='red'>PID {pid} 因 proxy 失敗略過：{err.__class__.__name__}</font></p>"
                        ))
                    except Exception:
                        pass
                finally:
                    self._release_account(acc, ok=ok)
            else:
                one = self.get_download_url(self.path, self.Agent, 1, pid)
```

Replace with:

```python
            if self._scheduler is not None:
                acc = self._acquire_account()
                if acc is None:
                    break  # stop signal or all disabled
                session = pixiv_api.make_session(acc.proxy_url)
                ok, one, _ = self._run_with_network_retry(
                    f"PID {pid}",
                    lambda: self.get_download_url(
                        self.path, self.Agent, 1, pid,
                        cookie_override=acc.cookie, session=session,
                    ),
                )
                self._release_account(acc, ok=ok)
            else:
                one = self.get_download_url(self.path, self.Agent, 1, pid)
```

`one` is `None` on retry exhaustion, which the existing `if isinstance(one, list): ... elif isinstance(one, str): ...` chain already handles (falls through to no-op).

- [ ] **Step 2: Run step-3 tests**

Run: `pytest tests/test_step3_url_helpers.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add app/core/thread_url_fetch.py
git commit -m "feat(retry): wire network retry into Step 3 (thread_url_fetch)"
```

---

## Task 4: Wire retry into Step 4 (thread_download metadata fallback)

**Files:**
- Modify: `app/core/thread_download.py:600-642` (`_load_artwork_metadata` scheduler branch only — leave the non-scheduler `else` branch untouched).

- [ ] **Step 1: Replace the scheduler branch**

In `app/core/thread_download.py`, in `_load_artwork_metadata`, find the scheduler-branch code that currently reads (around line 607-630):

```python
        if self._scheduler is not None:
            acc = self._acquire_account()
            if acc is None:
                return None
            ok = True
            try:
                self._record_cookie_usage("step3", pid_key, acc.cookie)
                session = pixiv_api.make_session(acc.proxy_url)
                try:
                    if need_cookie is False:
                        info = pixiv_api.Pixiv_info(url, self.agent, session=session)
                    else:
                        info = pixiv_api.Pixiv_info(
                            url, self.agent, cookie=acc.cookie, session=session,
                        )
                except (requests.exceptions.ProxyError,
                        requests.exceptions.ConnectTimeout,
                        requests.exceptions.ConnectionError):
                    ok = False
                    info = None
                except Exception:
                    info = None
            finally:
                self._release_account(acc, ok=ok)
```

Replace with:

```python
        if self._scheduler is not None:
            acc = self._acquire_account()
            if acc is None:
                return None
            self._record_cookie_usage("step3", pid_key, acc.cookie)
            session = pixiv_api.make_session(acc.proxy_url)

            def _do_fetch():
                if need_cookie is False:
                    return pixiv_api.Pixiv_info(url, self.agent, session=session)
                return pixiv_api.Pixiv_info(
                    url, self.agent, cookie=acc.cookie, session=session,
                )

            try:
                ok, info, _ = self._run_with_network_retry(
                    f"PID {pid_key}", _do_fetch,
                )
            except Exception:
                # Non-network exceptions: keep current "info=None, ok=True"
                # contract so this PID is treated as a missing-meta failure
                # (not a proxy disable).
                ok = True
                info = None
            self._release_account(acc, ok=ok)
```

The non-scheduler `else` branch directly below stays unchanged.

- [ ] **Step 2: Run step-4 tests**

Run: `pytest tests/ -k "step4 or download or metadata" -v --no-header -q | tail -40`
Expected: all pass (or fail only on tests already broken on this branch — verify against `git stash; pytest ...; git stash pop`).

- [ ] **Step 3: Commit**

```bash
git add app/core/thread_download.py
git commit -m "feat(retry): wire network retry into Step 4 metadata fallback"
```

---

## Task 5: Wire retry into Step 4 (thread_download single-thread PID-group loop)

**Files:**
- Modify: `app/core/thread_download.py:1545-1568`

- [ ] **Step 1: Replace the scheduler branch**

In `app/core/thread_download.py`, find the block in the single-thread download loop (around line 1545-1568):

```python
                if self._scheduler is not None:
                    acc = self._acquire_account()
                    if acc is None:
                        break  # stop signal or all disabled
                    # Sticky cookie: this PID's pages all use this cookie+proxy
                    pid_key = normalize_pid(pid) or str(pid)
                    self._pid_cookie_selection[pid_key] = acc.cookie
                    self._current_account = acc
                    ok = True
                    result = []
                    try:
                        result = self._download_pid_group(pid, pid_groups.get(pid, []))
                    except (requests.exceptions.ProxyError,
                            requests.exceptions.ConnectTimeout,
                            requests.exceptions.ConnectionError) as err:
                        ok = False
                        try:
                            self._q.put(WorkerEvent("output",
                                f"<p><font color='red'>PID {pid} 因 proxy 失敗略過：{err.__class__.__name__}</font></p>"))
                        except Exception:
                            pass
                    finally:
                        self._current_account = None
                        self._release_account(acc, ok=ok)
                    failed_nested.append(result if isinstance(result, list) else [])
```

Replace with:

```python
                if self._scheduler is not None:
                    acc = self._acquire_account()
                    if acc is None:
                        break  # stop signal or all disabled
                    # Sticky cookie: this PID's pages all use this cookie+proxy
                    pid_key = normalize_pid(pid) or str(pid)
                    self._pid_cookie_selection[pid_key] = acc.cookie
                    self._current_account = acc
                    ok, result, _ = self._run_with_network_retry(
                        f"PID {pid}",
                        lambda: self._download_pid_group(pid, pid_groups.get(pid, [])),
                    )
                    self._current_account = None
                    self._release_account(acc, ok=ok)
                    failed_nested.append(result if isinstance(result, list) else [])
```

- [ ] **Step 2: Run full Step 4 test set**

Run: `pytest tests/test_thread_download*.py tests/test_jxl_fallback.py -v --no-header -q | tail -50`
Expected: all pass.

- [ ] **Step 3: Run the entire test suite for regression**

Run: `pytest -m 'not integration' -q --no-header 2>&1 | tail -20`
Expected: all unit tests pass.

- [ ] **Step 4: Commit**

```bash
git add app/core/thread_download.py
git commit -m "feat(retry): wire network retry into Step 4 PID-group loop"
```

---

## Task 6: Update CLAUDE.md note (lightweight docs)

**Files:**
- Modify: `CLAUDE.md` — extend the "Per-account cooldown + proxy binding" section.

- [ ] **Step 1: Append retry semantics to CLAUDE.md**

In `CLAUDE.md`, in the section "Per-account cooldown + proxy binding (Steps 2/3/4)", add a new paragraph after the paragraph ending with "...mark that cookie disabled for the entire run.":

```markdown
On a `(ProxyError, ConnectTimeout, ConnectionError)` raised inside the four scheduler-aware call sites (Steps 2/3/4), the worker now retries on the **same account** up to **5 attempts total** with a fixed **60 s** wait between attempts (constants `NETWORK_RETRY_ATTEMPTS` and `NETWORK_RETRY_WAIT_SEC` in `app/core/pixiv_thread_base.py`). The retry is implemented in `PauseableThread._run_with_network_retry`. Only after all 5 attempts fail does the cookie get disabled via `release(ok=False)`. The 60 s wait is interruptible by `stop_event` and skipped during pause (paused time does not count toward the budget).
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note 5×60s network retry for Steps 2/3/4 in CLAUDE.md"
```

---

## Self-review (post-write check)

- **Spec coverage** — every requirement in `2026-05-02-network-retry-design.md` maps to a task: constants → Task 1; helper signature → Task 1; pause-pauses-the-clock → Task 1 step 3 + test 9; stop-aborts-immediately → Task 1 test 5+6; non-network propagates → Task 1 test 7; 4 call sites → Tasks 2-5; logging colors (`#b58900` yellow, `red` final) → Task 1 step 3.
- **Placeholder scan** — no TBD/TODO/"similar to" sections; every code block is complete.
- **Type consistency** — helper returns `(ok: bool, result, last_exc)` everywhere; all four call sites destructure with `ok, X, _`; `_run_with_network_retry` and `_wait_interruptible` are spelled identically across tasks.
