# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Before touching `app/gui/`, `app/core/thread_*.py`, or anything Flet-related,
> read `.claude/skills/flet-0-84-pitfalls/SKILL.md`.** It documents every API
> rename, threading hazard, and dialog-system change that bit during the
> PyQt5 → Flet 0.84 migration.

## Commands

Run the app (desktop):
```bash
python main.py
```

Run the app (web browser):
```bash
flet run app/gui/flet_app.py --web
```

Tests use `pytest` with one custom marker `integration` (configured in `pyproject.toml` under `[tool.pytest.ini_options]`) for tests that need network/real credentials.
```bash
pytest                                    # all unit tests (integration tests are not auto-excluded; pass -m 'not integration' to skip)
pytest tests/test_cookie_cooldown.py      # single file
pytest tests/test_jxl_fallback.py -k name # single test
pytest -m integration                     # live tests only
```

Quality / static-analysis tooling (configured in `pyproject.toml`, run from repo root):
```bash
ruff check app/                                          # lint (E/F/UP/B/SIM rules)
ruff check app/ --select UP --fix                        # auto-modernize syntax
radon cc app/ -n C -s                                    # cyclomatic complexity ≥ C
radon mi app/ -n B                                       # maintainability index ≤ B
lizard -C 15 -L 100 app/                                 # cognitive complexity / long-function warnings
vulture app/ vulture_whitelist.py --min-confidence 80    # dead code (whitelist for known false positives)
pylint --disable=all --enable=duplicate-code --min-similarity-lines=8 --recursive=y app/   # duplicate blocks
```
Reports from `Phase 24 baseline` and `Phase 26 final` live under `reports/`.

## Architecture

This is a Flet (Material 3) desktop/web app that scrapes Pixiv. The high-level pipeline is a 4-step workflow (`Step 1: following → Step 2: PIDs → Step 3: artwork URLs → Step 4: download`) which can also be chained via `Run All`. The GUI is completely Qt-free; all worker threads use `threading.Thread` and communicate via `queue.Queue`.

### Layered package layout (`app/`)

The canonical code lives under `app/` in three layers:

- `app/entry/main.py` — Flet bootstrap. Calls `ft.app(target=flet_main)` where `flet_main` is imported from `app.gui.flet_app`.
- `app/gui/` — Flet UI layer (no Qt imports anywhere in this directory).
  - `flet_app.py` defines `main(page: ft.Page)`. Builds the `NavigationRail` layout, instantiates `MainView` / `SettingsView` / `CookiesView`, creates the `event_q: queue.Queue`, wires `EventDispatcher`, and calls `page.run_thread(disp.run)`.
  - `dispatcher.py` — `EventDispatcher` polls `queue.Queue` every 50 ms via a background thread started with `page.run_thread`. Routes typed `WorkerEvent` payloads to registered handler callbacks which then call `page.update()`.
  - `views/main_view.py` — step buttons (1-4, Run All), log output panel, progress bar, countdown display, pause/resume/stop controls.
  - `views/settings_view.py` — download path, filter rules (ban/must tags, like threshold), wait-range spinners, JXL options.
  - `views/cookies_view.py` — cookie pool list, alias editing, add/remove/test-validity actions.
  - `log_format.py` — `html_to_spans()` converts HTML log lines (e.g. `<font color='red'>`) to `ft.TextSpan` lists for the Flet `ft.Text` widget.
  - `user_info.py` — persistence adapters (`Userdata_controller`, `othersettings`, `cookies_set`, `logging_mode_set`, `userpass`) that read/write JSON under `%APPDATA%/pixiv_download/`. No longer Qt-dependent.
- `app/core/` — network + heavy lifting (completely Qt-free).
  - `pixiv_thread_base.py` — `PauseableThread(threading.Thread)` base class with `pause()`, `resume()`, `stop()`, and `countdown()`. Pushes `WorkerEvent` objects onto a `queue.Queue` instead of emitting Qt signals.
  - `worker_event.py` — `WorkerEvent` frozen dataclass with fields `kind: str` and `data: object`.
  - `thread_following.py`, `thread_pid_scan.py`, `thread_url_fetch.py`, `thread_download.py` — the four worker threads, each extending `PauseableThread`. Instantiated directly by the view layer; the queue they share with the dispatcher is passed in at construction time.
  - `pixiv_api.py` wraps Pixiv HTTP endpoints, cookie handling, and response parsing.
  - `pixiv_thread_utils.py` is a helpers module: `atomic_write_json/text`, `normalize_pid`, `fetch_with_cookie_retry`, diagnostic event logging, PID cache sync.
  - `safe_io.py` provides atomic write + history-based backup (keeps latest 10 copies in a sibling `history/` directory).
  - `tag_edit.py`, `update_selenium.py` — tag filtering and Selenium cookie refresh.

### Top-level shim files

A few files at the repository root are thin re-exports that keep legacy absolute imports working — always edit the `app/` version, not the shim:

- `main.py` → `app.entry.main`
- `user_info.py` → `app.gui.user_info`
- `tag_edit.py` → `app.core.tag_edit`
- `update_selenium.py` → `app.core.update_selenium`

Note: the root `pixiv_api.py`, `pixiv_thread.py`, and `download_img.py` are still standalone copies (not shims) — the `app/core/*.py` versions are the ones loaded through `main.py → app.entry.main`. When in doubt, trace from `app/entry/main.py`; modules inside `app.core` import `from pixiv_api import *` and `import tag_edit` as bare names (not `app.core.*`), so `sys.path` must include the repo root (tests do this explicitly; `main.py` inherits it from being run at the repo root).

### Runtime data locations

All persisted settings and progress live under `%APPDATA%/pixiv_download/` (e.g. `cookies.json`, `othersettings.json`, `pictures_id.txt`, `pixiv_info_cache.json`). `safe_io.atomic_write_*` with `backup=True` (the default) copies the previous version into a sibling `history/` directory named `filename.YYYYMMDD[.N]`, keeping the latest 10; callers that should not leave a backup trail (notably `cookies.json`) pass `backup=False`.

### JXL post-processing

`thread_download` optionally converts downloaded images to JPEG XL using an external `cjxl.exe`. `_find_default_cjxl_path()` in `app/core/thread_download.py` searches known Windows paths (`~/Downloads/jxl*/bin/cjxl.exe`) as fallback; the settings view field `jxl_cjxl_path` overrides it; persisted `othersettings.json.jxl_*` keys are the final fallback. Keep behavior optional — absence of `cjxl.exe` must not break downloads (`tests/test_jxl_fallback.py`).

### Cookie pool

The app supports multiple cookies in rotation. The cookies view owns `cookies_pool` (list[str]) and `_cookie_alias_map` / `_cookie_status_map`. Entries are normalized (strip `Cookie:` prefix, dedupe) before being stored and passed to threads; downloader threads record per-PID cookie usage in `_pid_cookie_used`, which takes priority over `url_meta[pid].requires_cookie` (see `tests/test_cookie_cooldown.py`).

### Per-account cooldown + proxy binding (Steps 2/3/4)

`AccountScheduler` (`app/core/account_scheduler.py`) is a single-consumer round-robin state machine that gates HTTP work behind a per-account cooldown. Each `AccountState` holds `(cookie, alias, proxy_url, cooldown_until, disabled_reason)`; `proxies` property returns the `requests`-compatible dict via `app/core/proxy_utils.to_requests_proxies`. Workers in Steps 2/3/4 call `_acquire_account()` (blocks until next available) before each work unit and `_release_account(acc, ok=...)` after; `ok=False` (raised by `ProxyError` / `ConnectionError`) marks that cookie disabled for the entire run.

Settings keys driving this:
- `performance.pid_cooldown_avg` — single live-adjustable value (slider in settings UI). Each `release(ok=True)` schedules cooldown = `randint(int(avg*0.7), int(avg*1.3))` seconds. The settings UI warns when `< 30`.
- `auth.proxy_pool: list[str]` — multi-line proxy list edited in the settings "Proxy 設定" tile (`http://`, `https://`, `socks5://` URLs accepted; auto-detected via scheme).
- `auth.cookie_proxy_map: dict[cookie_str, proxy_url | None]` — bound in the cookies view's "Proxy 綁定" dropdown column. `None` = use local IP. Same account always uses same IP (hard contract).

`RunController._build_scheduler` (`app/gui/run_actions.py`) wires the scheduler from settings before launching n=2/3/4 threads; n=1 (`thread_following`) does not use a scheduler. The scheduler reads `pid_cooldown_avg` live (lambda over `_store()`) so a UI slider change takes effect on the next `release()`.

`pixiv_api.make_session(proxy_url)` builds a `requests.Session` with the bound proxy; `Pixiv_info(..., session=...)` keyword-only arg routes traffic through it. Step 2 (`thread_pid_scan`) passes `proxies=acc.proxies` directly to `requests.get`; Step 4 (`thread_download`) shares one session per PID across all multi-page downloads. ProxyError must propagate to the worker's release boundary — `Pixiv_info`, `gif_download`, `jpg_download`, `get_download_url`, and `thread_no_use_seleium_get_pid` all re-raise `(ProxyError, ConnectTimeout, ConnectionError)` before their broad `except Exception` handlers.

Deprecated: `cookie_speed_divisor` and `apply_cookie_pool_speedup` in `pixiv_thread_utils.py` are superseded by `AccountScheduler` and kept only for import compat.

## Conventions worth knowing

- User-facing strings and log/output messages are Traditional Chinese; keep that style when touching UI.
- Workers extend `PauseableThread` (which extends `threading.Thread`); never call network code on the GUI thread. Workers push `WorkerEvent(kind=..., data=...)` onto the shared `queue.Queue`; `EventDispatcher` routes them to the correct handler on the Flet event loop.
- Writes to shared state files go through `safe_io.atomic_write_*` or `pixiv_thread_utils.atomic_write_*`, not raw `open(..., "w")`, to survive interrupted runs.
