---
name: flet-0-84-pitfalls
description: |
  Concrete pitfalls encountered while migrating this app's GUI from PyQt5 to
  Flet 0.84. Use whenever editing app/gui/, app/core/thread_*.py, or any code
  that touches Flet controls, page.run_thread, page.run_task, FilePicker,
  AlertDialog, SnackBar, ExpansionTile, OutlinedButton/FilledButton text,
  worker thread pause/stop, or window-close handling. Almost every item below
  was a runtime bug the user hit, not theoretical advice.
---

# Flet 0.84 Migration Pitfalls

## Meta-rule (most important)

**Don't guess Flet API from training-data memory. Read the installed source first.**

The Flet API surface changed substantially between versions. In one session I
hit 8+ separate API breakages by trusting training-data recall. Always check:

- `C:/Users/Eric/.conda/envs/pixiv_env/Lib/site-packages/flet/controls/**/*.py`
  for control fields/signatures.
- `flet/messaging/session.py` for threading internals.
- `flet/controls/page.py` for `Page` properties / methods.
- `flet/controls/core/window.py` for window events.

Confirm version with the dist-info folder name (`flet-X.Y.Z.dist-info/`) not
`flet.__version__` (the user has rejected `python -c "import flet"` invocations).

---

## API renames / removals (Flet 0.84 specifics)

These are the literal errors that fired at `python main.py`:

| Old | New | Notes |
|-----|-----|-------|
| `ft.alignment.center` | `ft.Alignment(x=0, y=0)` | The `alignment` module-level constants are gone. |
| `ExpansionTile(initially_expanded=False)` | `ExpansionTile(expanded=False)` | Default is False — usually just drop the kwarg. |
| `FilePicker(on_result=...)` | `await fp.pick_files(...)` / `await fp.get_directory_path()` | Fully async return-value API; **no callback**. Handlers that use it must be `async def` and `await` the call. |
| `OutlinedButton(...).text = "..."` | `OutlinedButton(...).content = "..."` | Same for `FilledButton`. `text` silently sets an unknown attribute → diff is empty → no PATCH_CONTROL sent → UI never updates. **This bites silently** because Python lets you assign arbitrary attrs to dataclass instances. |
| `page.dialog = d; d.open = True; page.update()` | `page.show_dialog(d)` (and `page.pop_dialog()` to close) | Setting `page.dialog` is a silent no-op now. |
| `page.overlay.append(snack); snack.open = True` | `page.show_dialog(snack)` | `SnackBar` is now a `DialogControl`, not an overlay. Same for `Banner`, `BottomSheet`, `CupertinoBottomSheet`. |
| `page.overlay.append(file_picker)` | `page.services.append(file_picker)` (or `page.services.extend([...])`) | `FilePicker` was moved from `controls.material` to `controls.services`; it's a `Service`, not a `Control`. Putting it on `overlay` triggers `Unknown control: FilePicker`. |
| `page.snack_bar = ...` | Use `page.show_dialog(snack_bar)` | Same DialogControl change as above. |

**Verify by reading the dataclass**, not by guessing — e.g. `OutlinedButton`'s
`content: Optional[StrOrControl]` is the field; there is no `text` field.

---

## Threading model (THE biggest gotcha)

### Symptom

UI changes from background work only show up when the user pokes the window
(drag, click). Everything else looks "frozen" or "stuck at old value."

### Root cause

`session.patch_control()` ultimately does
`session.connection.send_queue.put_nowait(msg)` — and that send queue is an
**`asyncio.Queue`**. Calling `put_nowait` from a non-event-loop thread silently
fails to wake the consumer task; the patch sits in the queue until *any* event
on the loop happens to drain it.

### Fix

Run the dispatcher (and anything that calls `control.update()` /
`page.update()`) **on the asyncio event loop**, not in a thread:

```python
# WRONG — patches accumulate, only flushed when user interacts
page.run_thread(disp.run)              # disp.run is sync

# RIGHT — patches flush immediately
page.run_task(disp.run)                # disp.run is async def
```

`page.run_task(fn)` requires `fn` to be a coroutine function:

```python
async def run(self) -> None:
    while not self._stop_event.is_set():
        self._poll_once()
        await asyncio.sleep(0.05)      # yields to event loop
```

### When you must update UI from a worker thread

- Don't call `control.update()` / `page.update()` directly.
- Push a `WorkerEvent` onto the shared `queue.Queue` and let the async
  dispatcher handle it on the event loop.
- This is also why MainView's loading-overlay toggle goes through
  `WorkerEvent("loading", (busy, msg))` rather than calling
  `page.show_dialog()` from the daemon thread that runs the build phase.

---

## Sync click handlers run on the event loop

`base_control._trigger_event` `await`s sync handlers directly (line 474-478 of
flet 0.84 `base_control.py`). Anything blocking inside the click handler
freezes the entire UI:

- Disk I/O (e.g. `atomic_write_*` in pause/stop hooks) — move to
  `threading.Thread(target=..., daemon=True).start()`.
- Network calls — never from a click handler.
- Slow worker `__init__` (e.g. step 3 reading a large `all_url_meta.json`) —
  show the loading dialog first, then spawn a daemon thread for the actual
  build, then dismiss via the queue when done.

The pattern that works for "show modal spinner during slow prep":

```python
def _on_run_step(self, step):
    self._event_q.put(WorkerEvent("loading", (True, f"啟動 步驟 {step}")))
    threading.Thread(target=self._run_in_background,
                     args=(self._run_controller.run_step, step),
                     daemon=True).start()

def _run_in_background(self, fn, *args):
    try:
        fn(*args)
    finally:
        self._event_q.put(WorkerEvent("loading", (False, "")))
```

Loading on/off both go through the queue → dispatcher → handler so
`page.show_dialog` / `page.pop_dialog` always run on the event loop.

---

## Worker thread (PauseableThread) patterns

### `_pause_event.wait()` is a deadlock waiting to happen

Bare `self._pause_event.wait()` blocks indefinitely. If you set
`_stop_event` but the worker is currently inside `pause_event.wait`, it
won't exit unless something also sets `pause_event`.

Two-prong defense:
1. `PauseableThread.stop()` calls `self._pause_event.set()` to wake any
   waiter — keep that.
2. **Sleep loops should poll**, not block forever:

```python
# WRONG — blocks until resume(), even on stop
self._pause_event.wait()

# RIGHT — wakes within 0.5s on stop
while not self._pause_event.is_set():
    if self._stop_event.is_set():
        break
    self._pause_event.wait(timeout=0.5)
```

### `time.sleep(1)` in countdown loops ignores stop

```python
# WRONG — must wait 1 full second after stop before next iteration
time.sleep(1)

# RIGHT — wait() returns True the moment stop_event is set
if self._stop_event.wait(timeout=1.0):
    break
```

This applies to `_sleep_with_countdown` (base) and per-thread variants
(`thread_url_fetch._sleep_ultra_slow`, `thread_download._run_download_countdown`).

### Pause / stop hooks must not block the caller

`PauseableThread.pause()` calls `_on_pause_hook()` synchronously. For
`thread_following` that hook flushes `following.txt` + `following.json` (with
history backup). When the caller is the asyncio event loop (a click handler),
the disk I/O freezes the UI.

Fix in the base class — run hooks on a daemon thread:

```python
def pause(self):
    self._pause_event.clear()
    self._q.put(WorkerEvent("output", "<p><font color='red'>已暫停</font></p>"))
    threading.Thread(target=self._on_pause_hook, daemon=True).start()
```

### Progress events are deltas, not absolute values

Workers emit `WorkerEvent("progress", (delta, total))` — typically
`(1, pid_max)` per item processed, with `(0, total)` as a phase reset.
The MainView handler must accumulate locally:

```python
def update_progress(self, delta: int, total: int):
    if delta <= 0:
        self._progress_value = 0
    else:
        self._progress_value += int(delta)
    if total > 0:
        ratio = self._progress_value / total
        self._progress_bar.value = max(0.0, min(1.0, ratio))
```

Treating delta as the absolute current value pins the bar at `1/N` forever
(looks "stuck").

---

## Window close / process exit

### Symptom

Closing the window leaves the process hanging for 10–30 seconds before
exit.

### Root cause

`concurrent.futures.thread` registers an `atexit` hook that joins all
ThreadPoolExecutor workers. Step 1's `thread_following` uses
`ThreadPoolExecutor(max_workers=16)` with `requests.get(..., timeout=(10,30))`.
Even with `daemon=True`, the executor's atexit join waits for every in-flight
request to time out before the interpreter exits.

### Fix

Install a window close hook that stops cleanly, then `os._exit(0)` to bypass
the executor's atexit join:

```python
async def _shutdown_and_destroy():
    t = getattr(main_view, "_active_thread", None)
    if t and hasattr(t, "stop"):
        try: t.stop()
        except Exception: pass
    disp.stop()
    try:
        await page.window.destroy()
    except Exception:
        pass
    os._exit(0)            # bypass concurrent.futures atexit join

async def on_window_event(e):
    if getattr(e, "type", None) == ft.WindowEventType.CLOSE:
        await _shutdown_and_destroy()

page.window.prevent_close = True   # required for on_event to fire on close
page.window.on_event = on_window_event
page.on_disconnect = lambda e: _shutdown_and_destroy()  # web-mode equivalent
```

**Don't** drop the `prevent_close = True` — without it the `CLOSE` window
event isn't delivered to your handler.

Note: `page.on_close` is *session expiry*, not window close. Easy to confuse.

---

## Dialog content layout

`AlertDialog` requires at least one of `title`, `content`, or `actions` to be
non-empty/visible — otherwise you hit:

```
AlertDialog has nothing to display. Provide at minimum one of the following:
title, content, actions
```

For a "loading spinner" dialog, put a `Column([ProgressRing, Text(message)])`
in `content`. Don't try to use a fully empty dialog.

---

## ProgressBar + Text in a Row

`ft.Row([ft.ProgressBar(expand=True), text1, text2])` will squeeze `text1`
and `text2` to ~0 width if their `value` starts as `""`. Updates to `.value`
won't reflow the row reliably. Give the texts an explicit `width=` so they
keep their slot:

```python
self._progress_text = ft.Text("", width=120)
self._countdown_text = ft.Text("", width=140)
```

---

## Patch-diff sanity check

When `control.update()` doesn't visibly do anything:

1. Are you setting a real dataclass field (e.g. `content`, `value`) — not an
   arbitrary attribute (e.g. `text` on OutlinedButton)?
2. Is the call coming from the asyncio event loop thread? If from a worker
   thread, route via the event queue + async dispatcher instead.
3. Is the control attached to the page? `control.update()` raises if
   `not control.page` — but our `try/except: pass` would swallow that.
   Strip the except temporarily to see RuntimeErrors.
4. After the call, does the user have to drag the window for the change to
   appear? That's the asyncio.Queue-from-wrong-thread symptom.

---

## Quick reference: where things live in flet 0.84

```
flet/
├── controls/
│   ├── core/                       # layout primitives (Row, Column, Stack, ...)
│   ├── material/                   # Material widgets (Button, TextField, AlertDialog, SnackBar, ExpansionTile, Slider, ...)
│   ├── cupertino/                  # iOS-style widgets
│   ├── services/                   # FilePicker, Clipboard, UrlLauncher, ...
│   ├── core/window.py              # Window class + WindowEvent + WindowEventType
│   ├── page.py                     # Page (run_task, run_thread, services, window, on_disconnect, on_close, ...)
│   ├── base_page.py                # show_dialog / pop_dialog live here, not page.py
│   ├── dialog_control.py           # DialogControl base (open, on_dismiss)
│   └── base_control.py             # Control.update() entry point
└── messaging/
    └── session.py                  # patch_control, dispatch_event, send_message, asyncio.Queue
```
