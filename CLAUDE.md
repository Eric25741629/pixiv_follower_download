# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Run the app:
```bash
python main.py
```

Regenerate the Qt UI backup from the single source `test.ui` (only for reference; the app loads `.ui` directly):
```bash
python uimake.py    # pyuic5 -o trash/Ui2.py test.ui
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

This is a PyQt5 desktop app that scrapes Pixiv. The high-level pipeline is a 4-step workflow (`Step 1: following → Step 2: PIDs → Step 3: artwork URLs → Step 4: download`) which can also be chained via `Run All`.

### Layered package layout (`app/`)

The canonical code lives under `app/` in three layers:

- `app/entry/main.py` — Qt bootstrap. Creates the `QApplication`, applies `qfluentwidgets` light theme if available, instantiates `MainWindow_controller`, then calls `window.setup_control()`.
- `app/gui/` — Qt UI layer.
  - `controller.py` defines `MainWindow_controller`, a `FramelessMainWindow` subclass that loads `test.ui` at runtime via `uic.loadUi(...)` (no generated `Ui_*` class is imported in production; `trash/Ui2.py` is a reference backup only). All `on_<objectName>_clicked` slots and cookie-pool management live here.
  - `run_actions.py` is the workflow orchestrator. `start_get_following` / `start_get_pid` / `start_get_url` / `start_download` / `start_all` / `continue_all(num)` each build a `QThread` from `app.core.pixiv_thread`, wire its signals, and start it. The controller delegates button handlers into these.
  - `user_info.py` contains persistence adapters (`Userdata_controller`, `othersettings`, `cookies_set`, `logging_mode_set`, `userpass`) that read/write JSON under `%APPDATA%/pixiv_download/`.
- `app/core/` — network + heavy lifting (no Qt UI imports beyond `QThread`).
  - `pixiv_thread.py` holds the long-running workers as `QThread` subclasses: `get_following`, `get_pixiv_author_imgID_Thread`, `get_img_url_thread`, `download_thread`, `test_thread`. These are what `run_actions` instantiates.
  - `pixiv_api.py` wraps Pixiv HTTP endpoints, cookie handling, and response parsing.
  - `pixiv_thread_utils.py` is a helpers module: `atomic_write_json/text`, `normalize_pid`, `fetch_with_cookie_retry`, diagnostic event logging, PID cache sync.
  - `safe_io.py` provides atomic write + history-based backup (keeps latest 10 copies in a sibling `history/` directory).
  - `tag_edit.py`, `update_selenium.py` — tag filtering and Selenium cookie refresh.

### Top-level shim files

Several files at the repository root are thin re-exports that keep legacy absolute imports working — always edit the `app/` version, not the shim:

- `main.py` → `app.entry.main`
- `controller.py` → `app.gui.controller`
- `user_info.py` → `app.gui.user_info`
- `tag_edit.py` → `app.core.tag_edit`
- `update_selenium.py` → `app.core.update_selenium`

Note: the root `pixiv_api.py`, `pixiv_thread.py`, and `download_img.py` are still standalone copies (not shims) — the `app/core/*.py` versions are the ones loaded through `main.py → app.entry.main`. When in doubt, trace from `app/entry/main.py`; modules inside `app.core` import `from pixiv_api import *` and `import tag_edit` as bare names (not `app.core.*`), so `sys.path` must include the repo root (tests do this explicitly; `main.py` inherits it from being run at the repo root).

### UI source of truth

`test.ui` (Qt Designer) is the sole UI structure source. `controller.py` loads it with `uic.loadUi((repo_root / "test.ui"))` at `__init__` time, so widget object names in `.ui` are used directly as attributes (e.g. `self.ui.like_num`, `self.ui.cookies_input`, `self.ui.settings_tabs`, `self.ui.jxl_enable`). Any `objectName` change in `test.ui` must be mirrored in `controller.py`, `run_actions.py`, and `user_info.py` (see `UI_REFACTOR_IMPLEMENTATION_PLAN.md`). Do not hand-edit `trash/Ui2.py`; regenerate it with `uimake.py` if needed.

### Runtime data locations

All persisted settings and progress live under `%APPDATA%/pixiv_download/` (e.g. `cookies.json`, `othersettings.json`, `pictures_id.txt`, `pixiv_info_cache.json`). `safe_io.atomic_write_*` with `backup=True` (the default) copies the previous version into a sibling `history/` directory named `filename.YYYYMMDD[.N]`, keeping the latest 10; callers that should not leave a backup trail (notably `cookies.json`) pass `backup=False`.

### JXL post-processing

`download_thread` optionally converts downloaded images to JPEG XL using an external `cjxl.exe`. `run_actions._find_default_cjxl_path()` searches known Windows paths (`~/Downloads/jxl*/bin/cjxl.exe`) as fallback; the UI field `jxl_cjxl_path` overrides it; persisted `othersettings.json.jxl_*` keys are the final fallback. Keep behavior optional — absence of `cjxl.exe` must not break downloads (`tests/test_jxl_fallback.py`).

### Cookie pool

The app supports multiple cookies in rotation. `MainWindow_controller` owns `cookies_pool` (list[str]) and `_cookie_alias_map` / `_cookie_status_map`. Entries are normalized (strip `Cookie:` prefix, dedupe) in both `controller._normalize_cookie_pool` and `run_actions._get_cookie_payload`; downloader threads record per-PID cookie usage in `_pid_cookie_used`, which takes priority over `url_meta[pid].requires_cookie` (see `tests/test_cookie_cooldown.py`).

## Conventions worth knowing

- User-facing strings and log/output messages are Traditional Chinese; keep that style when touching UI.
- Workers extend `QThread`; never call network code on the GUI thread. `run_actions._connect_common` is the standard place to wire `countdown`, `timechanged`, and `thenext` signals.
- Writes to shared state files go through `safe_io.atomic_write_*` or `pixiv_thread_utils.atomic_write_*`, not raw `open(..., "w")`, to survive interrupted runs.
