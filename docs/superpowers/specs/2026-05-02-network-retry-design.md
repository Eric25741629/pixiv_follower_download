# Network Retry on ProxyError / ConnectTimeout / ConnectionError

**Date**: 2026-05-02
**Status**: Approved (decisions confirmed in chat)

## Problem

Today, when a worker hits `(requests.exceptions.ProxyError,
requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError)`,
it calls `self._release_account(acc, ok=False)` once. `AccountScheduler.release(ok=False)`
immediately marks `account.disabled_reason = "proxy_dead"`. With a single
Cookie/account in the pool, the next `acquire()` finds zero active
accounts and returns `None`, emitting `"所有 Cookie 都已禁用，任務停止"`. A
single transient network blip therefore ends the entire run.

## Goal

On the network-triple exception, retry the failing call up to **5 times
total** (1 original + up to 4 retries — see "Counting" below), waiting
**60 s** between attempts, on the **same account/proxy**, before falling
back to the existing `release(ok=False)` disable path.

## Scope

Apply to all four worker call sites that currently catch the network
triple. Each catches the same exception family and is followed by an
unconditional `_release_account(acc, ok=ok)` in `finally`:

| Step | File | Line (current) | Work unit |
|---|---|---|---|
| 2 | `app/core/thread_pid_scan.py` | 111 (`_run_step2_with_acquired_cookie`) | one artist id |
| 3 | `app/core/thread_url_fetch.py` | 1269 (`_run_processing_loop`) | one PID URL fetch |
| 4 (meta fallback) | `app/core/thread_download.py` | 622 (`_load_artwork_metadata` scheduler branch) | one PID metadata refetch |
| 4 (PID group) | `app/core/thread_download.py` | 1557 (single-thread download loop) | one PID's pages |

Out of scope:
- Step 1 (`thread_following`) — not scheduler-routed, not in the
  per-account proxy path.
- Non-scheduler branches (`else: ...` paths in those four files) — they
  do not have an `acc` to release, so the retry contract does not apply.
- Inner per-image retries inside `jpg_download` / `gif_download`
  (different layer; those re-raise the network triple up to the
  scheduler-aware caller, which is the layer this spec addresses).

## Constants

Hardcoded module-level constants — no settings UI:

```python
NETWORK_RETRY_ATTEMPTS = 5      # total attempts (1 original + 4 retries)
NETWORK_RETRY_WAIT_SEC = 60     # sleep between attempts
```

These live in `app/core/pixiv_thread_base.py` (the common base class for
all four workers) so each call site imports the same numbers.

## Counting

`NETWORK_RETRY_ATTEMPTS = 5` means **5 attempts in total**. After the
1st attempt fails, log `第 1/5 次失敗，60 秒後重試`. After the 5th
attempt fails, log `重試 5 次仍失敗，停用此 Cookie` and proceed with
`release(ok=False)`. Successful attempt breaks the loop and proceeds
with `release(ok=True)` as today.

## Retry contract

- **Same account**: the loop reuses the `acc` returned by the original
  `_acquire_account()`. We do **not** re-acquire from the scheduler
  between attempts. This keeps the throughput gate uncharged for retries
  and keeps the same proxy binding (matching the "same cookie → same
  IP" hard contract).
- **Interruptible sleep**: the 60 s wait must respond to stop/pause.
  Use the same pattern as existing waits — poll `stop_event` /
  `pause_event` every 0.5 s. If `stop_event.is_set()` during the wait,
  break out, mark `ok=False`, and `release` immediately so the run can
  shut down promptly. If `pause_event` clears (paused), block on it
  without consuming retry attempts (paused time does not count toward
  the 60 s budget — when resumed, the remaining wait continues).
- **Exception filter**: only the network triple
  `(ProxyError, ConnectTimeout, ConnectionError)` triggers retry. Every
  other exception (including `requests.exceptions.ReadTimeout`,
  `HTTPError`, generic `Exception`) propagates as before.
- **Logging**: yellow `<font color='#b58900'>` for "第 N/5 次失敗" lines,
  red `<font color='red'>` for the final "重試 5 次仍失敗" line. Use the
  exception class name in the message (matches existing format).

## Implementation sketch

A shared helper on `PauseableThread` (defined in `pixiv_thread_base.py`)
encapsulates the loop. Each call site replaces its current
try/except/finally with one call to this helper.

```python
# pixiv_thread_base.py
NETWORK_RETRY_ATTEMPTS = 5
NETWORK_RETRY_WAIT_SEC = 60
_NETWORK_RETRY_EXCEPTIONS = (
    requests.exceptions.ProxyError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
)

def _run_with_network_retry(self, work_label: str, fn):
    """Run fn() with up to NETWORK_RETRY_ATTEMPTS attempts.

    Returns (ok: bool, result, last_exc). On success: (True, value, None).
    On exhaustion: (False, None, last_exc). Caller is responsible for
    release(ok=...). Stop signal during wait returns (False, None, None)
    immediately so the worker can break its outer loop.

    work_label is included in log lines (e.g. "畫師 12345" or "PID 67890").
    """
    last_exc = None
    for attempt in range(1, NETWORK_RETRY_ATTEMPTS + 1):
        if self._stop_event.is_set():
            return False, None, last_exc
        try:
            return True, fn(), None
        except _NETWORK_RETRY_EXCEPTIONS as err:
            last_exc = err
            if attempt < NETWORK_RETRY_ATTEMPTS:
                self._emit_output(
                    f"<p><font color='#b58900'>{work_label} 第 {attempt}/"
                    f"{NETWORK_RETRY_ATTEMPTS} 次失敗（{err.__class__.__name__}），"
                    f"{NETWORK_RETRY_WAIT_SEC} 秒後重試</font></p>"
                )
                if not self._wait_interruptible(NETWORK_RETRY_WAIT_SEC):
                    return False, None, last_exc  # stop fired
            else:
                self._emit_output(
                    f"<p><font color='red'>{work_label} 重試 "
                    f"{NETWORK_RETRY_ATTEMPTS} 次仍失敗（{err.__class__.__name__}），"
                    f"停用此 Cookie</font></p>"
                )
    return False, None, last_exc
```

`_wait_interruptible(seconds)` returns `True` if the full duration
elapsed, `False` if `stop_event` fired. Uses 0.5 s polling and honours
`pause_event` (paused time does not count toward `seconds`).

`_emit_output(html)` is a thin wrapper around the existing
`self._q.put(WorkerEvent("output", html))` pattern.

## Call site refactor (sample, step 2)

Before:
```python
try:
    result = self.thread_no_use_seleium_get_pid(...)
except (ProxyError, ConnectTimeout, ConnectionError) as err:
    ok = False
    self._q.put(WorkerEvent("output", f"<p>...略過：{err.__class__.__name__}</p>"))
finally:
    self._release_account(acc, ok=ok)
```

After:
```python
ok, result, _ = self._run_with_network_retry(
    f"畫師 {aid}",
    lambda: self.thread_no_use_seleium_get_pid(
        acc.cookie, self.Agent, self.path, '1', aid, proxies=acc.proxies,
    ),
)
self._release_account(acc, ok=ok)
```

The other three sites follow the same shape with different
`work_label` values (`PID {pid}` for steps 3 and 4) and different
`fn` lambdas.

## Tests

New unit test file `tests/test_network_retry.py`:
1. Success on first attempt → fn called once, returns `(True, value, None)`.
2. Success on attempt 3 → fn called 3 times, returns `(True, value, None)`,
   2 yellow log lines emitted.
3. All 5 attempts fail → fn called 5 times, returns `(False, None, exc)`,
   4 yellow + 1 red log lines emitted.
4. Stop event fires during wait → returns `(False, ...)` promptly without
   consuming all retries.
5. Non-network exception (e.g. `ValueError`) propagates unchanged on
   first occurrence — not caught by retry loop.
6. Pause event during wait → wait clock pauses; resume continues from
   where it stopped (verifiable by mocking time and checking total
   sleep budget).

Existing tests that must keep passing:
- `tests/test_account_scheduler.py` — `release(ok=False)` semantics
  unchanged.
- `tests/test_thread_base.py::test__release_account_*` — release
  behaviour unchanged.
- `tests/test_step3_url_helpers.py` — step 3 still releases
  `ok=False` after 5 attempts.

## Risk / non-goals

- **Run time**: a sustained outage on a single-cookie pool now spends
  up to 5 × 60 s = 5 min before stopping (versus stopping immediately
  today). This is the user's explicit intent.
- **No exponential backoff**: 60 s flat between attempts as requested.
- **No cross-account fallback**: even if other accounts are free, we
  retry the same one. Spec confirmed in chat.
- **Inner retries unchanged**: `jpg_download` / `gif_download` still
  re-raise network triple to the scheduler-aware caller (per Phase 23
  contract). Only the outer scheduler-aware layer adds the 60 s × 5
  loop.
