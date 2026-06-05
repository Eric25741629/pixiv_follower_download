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
  - `worker_event.py` — `WorkerEvent` frozen dataclass with fields `type: str` and `data: Any`, constructed positionally (e.g. `WorkerEvent("output", html)`). Note: the attribute is `type`, not `kind` — read it as `ev.type` / `getattr(ev, "type", None)`.
  - `thread_following.py`, `thread_pid_scan.py`, `thread_url_fetch.py`, `thread_download.py` — the four worker threads, each extending `PauseableThread`. Instantiated directly by the view layer; the queue they share with the dispatcher is passed in at construction time.
  - `pixiv_api.py` wraps Pixiv HTTP endpoints, cookie handling, and response parsing.
  - `pixiv_thread_utils.py` is a helpers module: `atomic_write_json/text`, `normalize_pid`, `fetch_with_cookie_retry`, diagnostic event logging, PID cache sync.
  - `safe_io.py` provides atomic write + history-based backup (keeps latest 10 copies in a sibling `history/` directory).
  - `update_selenium.py` — Selenium-based cookie refresh.

### Top-level shim files

A few files at the repository root are thin re-exports that keep legacy absolute imports working — always edit the `app/` version, not the shim:

- `main.py` → `app.entry.main`
- `user_info.py` → `app.gui.user_info`
- `update_selenium.py` → `app.core.update_selenium`

Note: the root `pixiv_api.py`, `pixiv_thread.py`, and `download_img.py` are still standalone copies (not shims) — the `app/core/*.py` versions are the ones loaded through `main.py → app.entry.main`. When in doubt, trace from `app/entry/main.py`; modules inside `app.core` use `from pixiv_api import *` as a bare name (not `app.core.pixiv_api`), so `sys.path` must include the repo root (tests do this explicitly; `main.py` inherits it from being run at the repo root).

### Runtime data locations

All persisted settings and progress live under `%APPDATA%/pixiv_download/` (e.g. `cookies.json`, `othersettings.json`, `pictures_id.txt`, `pixiv_info_cache.json`). `safe_io.atomic_write_*` with `backup=True` (the default) copies the previous version into a sibling `history/` directory named `filename.YYYYMMDD[.N]`, keeping the latest 10; callers that should not leave a backup trail (notably `cookies.json`) pass `backup=False`.

### Canonical persistence schema (`metadata.sqlite3`)

`%APPDATA%/pixiv_download/metadata.sqlite3` is the canonical store. Two tables drive every workflow decision:

- `artworks (pid PK, discovered_at, page_count, like_count, tags, img_url_template, requires_cookie, meta_updated_at, revoked_at)` — one row per Pixiv ID the app has ever seen. `meta_updated_at IS NULL` means Step 3 still needs to fetch meta; `revoked_at IS NOT NULL` means the PID was 404'd by Pixiv (or imported as "do not process").
- `pages (pid, page_index, status, url, file_path, file_size, downloaded_at, last_attempted_at, attempt_count, failure_reason) PK (pid, page_index)` — one row per (PID, page) tuple. `status ∈ {pending, downloaded, failed, revoked}`. Step 4's queue is `WHERE status='pending'`.

Three views provide the decisions Step 3 / Step 4 consult:

- `v_pending_artworks` — PIDs Step 3 still needs to fetch meta for (`meta_updated_at IS NULL AND revoked_at IS NULL`).
- `v_pending_pages` — `(pid, page_index, url)` tuples for Step 4 to download.
- `v_complete_artworks` — PIDs whose meta is known *and* every page is on disk; used to short-circuit re-downloads.
- `v_closed_artworks` — superset of `v_complete_artworks` plus revoked PIDs plus legacy-sentinel PIDs (imported from `exist_pid.json` with no evidence of pending work). This is the set Step 4 uses as `exist_pid` for filtering.

Helpers in `app/core/metadata_db.py`: `upsert_artwork`, `upsert_artworks` (bulk), `mark_artwork_revoked`, `upsert_page`, `mark_page_downloaded`, `mark_page_failed`, `mark_page_pending`, `upsert_pages_bulk`, `get_pending_pages`, `get_pending_urls_filtered`, `closed_artwork_set`, `is_pid_closed`, `is_pid_complete`, `page_status_counts`.

**Legacy tables (`pids`, `downloaded`, `pending_urls`, `pending_pids`) were dropped in Phase 8.** `_SCHEMA` runs `DROP TABLE IF EXISTS` on every open (`app/core/metadata_db.py:38-41`) and nothing writes to them — the `MetadataDB` methods named after them (`upsert_pending_urls`, `upsert_pending_pids`, `mark_url_done`/`mark_urls_done`) write the canonical `pages` / `artworks` tables instead, so legacy-table write-amplification is zero. (This is separate from the still-active PHASE-A `exist_pid` shadow-write into `artworks` sentinel rows described below.) JSON / text files `exist_pid.json`, `all_url.txt`, `pictures_id.txt`, `err_url.txt` likewise persist for compatibility but the canonical state lives in SQLite.

#### `exist_pid` DB-only migration (in progress — search `PHASE-A`)

Phase A (current): `sync_exist_pid_with_download_folder` writes scanned PIDs to BOTH `exist_pid.json` AND `metadata.sqlite3` via `_shadow_write_exist_pid_to_db` → `mirror_exist_pid_set` → sentinel rows in `artworks` that `v_closed_artworks` picks up. Steps 2/3 still read `load_exist_pid_set(path)` (JSON ∪ DB). Step 4 already uses `db.closed_artwork_set()` directly.

Phase B (planned): switch `run_actions._build_step2/_build_step3` to `MetadataDB(path).closed_artwork_set()`, drop the JSON `atomic_write_json` in `sync_exist_pid_with_download_folder`, delete `load_exist_pid_set` + its `_read_exist_pid_json` / `_read_legacy_exist_pid_set` / `_trash_legacy_exist_pid_files` / `_augment_exist_pid_from_db` helpers, and stop maintaining the `exist.json` / `existPID.txt` fallback paths.

`grep PHASE-A` enumerates the migration surface — both the shadow-write hooks added in Phase A and the JSON-only branches that should disappear in Phase B.

Migration utility: `python tools/db_migration.py [--dry-run]` (idempotent — re-running picks up nothing on a fresh DB). `python tools/dump_state.py` snapshots all sources as JSON for diff. `python tools/verify_consistency.py` cross-checks legacy vs new tables.

### Event log + replay

Every mutation to canonical `artworks` / `pages` tables is also appended as one JSON line to `%APPDATA%/pixiv_download/events/events-YYYYMMDD.jsonl` **before** the DB write. Files rotate by date AND by size (`event_log.rotate_size_bytes`, default 128 MB → `events-YYYYMMDD.NNN.jsonl`, zero-padded sequence; the bare name is sequence 0). Retention is time-based (`event_log.retention_days`, 60) AND byte-capped (`event_log.max_total_bytes`, default 4 GB — oldest files evicted first, never past the most recent snapshot/shutdown/checkpoint anchor).

**Durability cadence:** every line is `flush()`ed (survives a process kill), but `os.fsync` is batched — forced every `event_log.fsync_every_n` events (default 200) OR `event_log.fsync_interval_sec` (default 1.0s), whichever first, plus unconditionally on anchor kinds and `close()`. Set `fsync_every_n=1` for the legacy per-event fsync (max power-loss durability). The batched default removes the per-DB-mutation disk barrier that dominated write cost; the SQLite WAL (`synchronous=NORMAL`) is the authoritative durable store, the log is a recovery aid.

Emitted by `MetadataDB._emit(...)` in `app/core/metadata_db.py`. Event kinds:
- `page.upsert` — every `upsert_page` (covers `mark_page_downloaded` / `_failed` / `_pending` via convenience wrappers that call `upsert_page` underneath; no separate event for those)
- `pages.upsert_bulk` — `upsert_pages_bulk`
- `pages.downloaded_bulk` — `mark_pages_downloaded_bulk` (Step-4 success path via `mark_urls_done`). Replays through an `ON CONFLICT DO UPDATE SET status='downloaded'`, so recovery correctly flips pre-seeded `pending` rows to `downloaded` (a plain `pages.upsert_bulk` uses `INSERT OR IGNORE` and would leave them stuck pending)
- `artwork.upsert` — `upsert_artwork` (also emitted per-PID by `import_downloaded_set` so PHASE-A shadow-write inserts can be replayed)
- `artwork.discovered` — `upsert_artworks` (bulk PID discovery)
- `artwork.revoked` — `mark_artwork_revoked`
- `session.start` / `session.shutdown` — anchor events emitted by `EventLog.__init__` / `close`
- `snapshot` — emitted after a successful `MetadataDB.backup_db()` (called daily by `RunController._backup_db`, throttled via `othersettings.event_log.last_snapshot_date`; `max_history` = `max(3, retention_days // 14)`). After a verified snapshot, `RunController._backup_db` calls `EventLog.compact_before_date` to prune event files fully older than it (2-day margin), bounding the log to a small tail by construction
- `checkpoint` — a lightweight anchor (no DB copy) emitted at startup by `app/gui/flet_app.py` **after** `recover_tail`, so the next crash recovery is bounded to one session even when the user never presses Run and force-kills

Cutoff anchors (what `recover_tail` stops at) are `session.shutdown` / `snapshot` / `checkpoint`.

Two recovery paths:
- **Automatic** — `app/gui/flet_app.py` startup constructs the EventLog and, if `last_session_was_unclean`, calls `recover_tail(db, log_dir)`. It **reverse-seeks** (block-streamed, never `readlines` a multi-GB file) from the newest file to the most recent cutoff anchor and applies only the orphan tail — O(tail), not O(total log), so a large backlog can never blank-screen startup. The tail scan is bounded by `RECOVER_TAIL_MAX_BYTES` (256 MB); beyond it the most-recent budget-worth is applied best-effort and a warning logged. All DB mutation methods are idempotent (`INSERT OR IGNORE` / `ON CONFLICT DO UPDATE`), so re-application is safe. During recovery the DB's `_event_log` is temporarily set to None to prevent emit-loops.
- **Manual** — `python tools/replay_events.py [--target PATH] [--from-snapshot PATH] [--dry-run]` rebuilds a fresh DB from the latest `history/metadata.sqlite3.YYYYMMDD` snapshot plus events newer than it. Use when the live DB is unrecoverable.

To disable the event log entirely, set `othersettings.event_log.enabled = false` (`MetadataDB(path, event_log=None)` reverts to SQLite-WAL-only durability).

### JXL post-processing

`thread_download` optionally converts downloaded images to JPEG XL using an external `cjxl.exe`. `_find_default_cjxl_path()` in `app/core/thread_download.py` searches known Windows paths (`~/Downloads/jxl*/bin/cjxl.exe`) as fallback; the settings view field `jxl_cjxl_path` overrides it; persisted `othersettings.json.jxl_*` keys are the final fallback. Keep behavior optional — absence of `cjxl.exe` must not break downloads (`tests/test_jxl_fallback.py`).

### Author-ordered downloads (Step 4)

Optional `download.author_order` (bool, default false; UI switch 「依作者順序下載（同作者連續）」 in settings) makes Step 4 download one author's works contiguously before the next. The pure, module-level `compute_author_order(pid_order, {pid: user_id})` in `app/core/thread_download.py` reorders the `pid_order` from `_group_urls_by_pid` into `(flat_order, author_batches)`: authors sequenced by **first-encounter** order, within-author by **PID descending** (`_leading_pid_int`, robust to hash-form pids), **unknown author** (NULL/empty `artworks.user_id`) bucketed **last**. `_resolve_execution_order` gates it — off → a single batch identical to prior behavior (zero regression). Pool mode `_execute_downloads_pool` drains each author batch (`as_completed`) before submitting the next (strict per-author barrier); single-thread mode is inherently strict on `flat_order`. `user_id` is read in bulk via `MetadataDB.user_id_map_for_pids` (keys on the original pid string, chunks at 900). Wired `_LEGACY_SCALAR_KW_SCHEMA` → `download_thread.__init__` (`self.author_order`) → `run_actions._build_step4`, same path as `tag_strip_brackets`. Tests: `tests/test_compute_author_order.py`, `test_execute_downloads_pool_author_barrier.py`, `test_reorder_pid_order_by_author.py`, `test_user_id_map_for_pids.py`, `test_author_order_wiring.py`.

### Combined fetch-download (邊查邊下)

Optional `download.combined_mode` (bool, default false; UI switch 「邊查邊下（查到即下載，合併步驟三、四）」 in settings) merges Step 3 (query meta) and Step 4 (download) into a single per-PID pass so that resolving a PID's meta and downloading its pages both happen inside **one account cooldown window** instead of two separate full-pipeline passes. The new thin orchestrator `combined_thread(PauseableThread)` in `app/core/thread_combined.py` **composes** a `get_img_url_thread` (query engine, `self.fetcher`) and a `download_thread` (download engine, `self.downloader`) as helpers — it never calls their `run()`. The two engines share one event queue, one `AccountScheduler` (propagated post-construction via `_share_scheduler`), one `_pause_event` / `_stop_event`, and one `_metadata_db` connection (downloader's DB is reassigned to the fetcher's).

The work queue is built by `_build_work_lists()` → `(query_pids, download_only_pids)`. `query_pids` come from `pictures_id.txt` via the fetcher's pure filter helpers (`check_exist` + `_prepare_pending_pid_tasks`, deliberately **not** `_load_and_filter_pid_list`, to avoid its next/progress emits; it also seeds the fetcher's pending-PID tracker so finalize doesn't blank `pictures_id.txt`). `download_only_pids` are PIDs with pending pages in the DB (`MetadataDB.pids_with_pending_pages()` reading `v_pending_pages`) that are **not** already in `query_pids` — this auto-absorbs a partial Step 3 that resolved meta but never downloaded. Their per-page URLs are grouped once from `get_pending_pages()` into `_pending_urls_by_pid` (read via `_download_only_urls`), avoiding an O(D×P) re-scan per PID.

`_process_one_pid(pid, needs_query)` acquires one account, runs the query (via `_run_with_network_retry` + `fetcher.get_download_url`) only when `needs_query`, **seeds the canonical `pages` rows** for the resolved URLs (`db.upsert_pending_urls`) before downloading them through `downloader._download_pid_group` — also wrapped in `_run_with_network_retry`, so a proxy error on the download leg retries (and disables the cookie via `release(ok=False)`) instead of aborting the whole run. On a clean download it marks them done (`db.mark_urls_done`), flushes the closed sentinel (`_maybe_flush_exist_pid`), and **persists that PID's meta immediately** (`_persist_pid_meta` → `import_meta_dict`) so `page_count` + `meta_updated_at` land at once and `v_complete_artworks` / `v_closed_artworks` are exact and crash-safe. `ok` / `download_ok` are initialised before the `try` so the `finally` `_release_account(ok=ok and download_ok)` is always valid; `_mark_pid_processed` runs only on genuine per-PID success, so an exhausted query or partial-download failure stays pending for the next run. `run()` emits 「邊查邊下階段開始」, iterates query-then-download-only, finalizes — fetcher: `_flush_url_meta_snapshot` / `_persist_pending_pid_file` / `_flush_revoked_pid_file`; downloader: **failure recording only** (`_classify_download_results` → `err_url.txt` + `_shadow_mark_failures`), deliberately **not** `_finalize_downloads` (which would clobber `all_url.txt` from the now-empty `allurl`) — and emits a **terminal `WorkerEvent("next", -1)`** so `Run All` stops after combined mode rather than chaining into a separate Step 4.

The downloader is constructed with the opt-in `defer_step4_scan=True` + `db_base_path` (new keyword-only `download_thread.__init__` params; default off = byte-identical Step 4). Under `defer_step4_scan` the constructor skips the Step-4-only heavy init (`_load_initial_exist_pid_set` / `_mirror_exist_pid_to_db` / `_emit_metadata_db_stats` / `_warn_if_meta_empty_with_like_filter` / `_read_all_url_file_into_state` / `_prepare_download_tasks`, setting safe defaults `exist_pid=set()` / `allurl=[]` / `pid_max=0`) that otherwise scans the full metadata DB at construction — a ~40 s freeze on a large DB. `db_base_path` opens the metadata DB at the combined base path so the downloader shares the fetcher's DB from the start.

Routed in `RunController._build_thread(3)`: when `download.combined_mode` (in `DEFAULTS["download"]`, default False) is on **or** `RunController.force_combined` is set (the CLI `run --step combined` path), `_build_combined` (mirrors the `_build_step3` + `_build_step4` arg assembly) is returned instead of `_build_step3`; off → unchanged Step 3 (zero regression). Tests: `tests/test_metadata_pids_with_pending.py`, `test_combined_mode_wiring.py`, `test_combined_work_queue_absorbs_pending.py`, `test_combined_one_cooldown_per_pid.py`, `test_combined_cache_hit_no_network.py`, `test_combined_run_all_terminal_next.py`, `test_combined_query_seeds_and_marks_db.py`.

### Headless CLI + in-app scheduler

The same `RunController` pipeline can run with no Flet GUI. `RunController`'s only GUI coupling is three things on its view (`set_running(bool)`, `set_step_state(i, state)`, and read/write of `_active_thread`); `HeadlessView` in `app/cli/headless_view.py` is a tiny stub satisfying exactly that surface so the controller runs unchanged headless.

`run_headless(step)` in `app/cli/headless_runner.py` mirrors `flet_app.main`: it builds the event `queue.Queue` + an `EventLog` (with the same crash-recovery `recover_tail` + `checkpoint` emit), constructs `RunController(HeadlessView(), event_q, event_log=...)`, kicks off the action, then pumps the queue via `_pump`. `step` is one of `{1,2,3,4,combined,all}`: `all` → `controller.run_all()`; `combined` → sets `controller.force_combined = True` then `run_step(3)` (the `force_combined` flag, honored in `_build_thread(3)`, forces `_build_combined` even when `download.combined_mode` is off); `1`/`2`/`3`/`4` → `run_step(int(step))`. `_pump` reads `WorkerEvent.type` / `.data` (the dataclass field is `type`, not `kind`), prints `output`/`finished` text to **stderr** (HTML stripped via `_strip_html`), and forwards `next n` to `controller.on_next(n)` so Run-All chains. Terminal detection disambiguates the overloaded `next == -1` (both a failed step's error terminal AND combined mode's normal terminal) by whether a `finished` immediately preceded it: single step exits 0 on `finished` / 1 on `next == -1`; run_all exits 0 when the pipeline reaches step ≥ 4 `finished` or when `finished` precedes `next == -1` (combined success), and 1 when `next == -1` arrives with no preceding `finished` (step failure). Queue starvation (`Empty` after 600 s) exits 2.

`cli.py` (repo root) is a thin entry calling `app.cli.commands.main`; `python cli.py <subcommand>`. Subcommands (`app/cli/commands.py`, argparse `prog="pixiv-cli"`):
- `run --step {1,2,3,4,combined,all}` — delegates to `run_headless`; its process exit code is the pump's.
- `status [--json]` — reads `MetadataDB.page_status_counts()` + `meta_count()`; JSON keys `pending_pages` / `downloaded_pages` / `failed_pages` / `revoked_pages` / `meta_count` / `db_path`.
- `config get|set <section>.<field> [value] [--json]` — `set` infers the stored type from the existing/default value (`_coerce_like`; bool/int/list), falling back to `_infer_from_literal` (bool/int/string) for keys absent from `DEFAULTS`.
- `cookie test [--json]` — runs `pixiv_api.Test_cookies` per configured cookie; JSON `tested` / `valid`; exit 0 iff at least one valid.
- `following export [--json]` — prints `run_actions._load_author_list()`.

CLI contract: read commands print JSON to **stdout** (with `--json`); all human/log text goes to **stderr**; exit codes are meaningful (0 ok, non-zero on error). `APPDATA` selects the data dir (tests set it to a tmp dir), so the CLI shares the GUI's `%APPDATA%/pixiv_download/` state.

`SchedulerService` (`app/core/scheduler_service.py`) is a daemon thread that fires Run All on the `schedule` settings section (`DEFAULTS["schedule"]`: `enabled` False, `mode` `"daily"|"interval"`, `time` `"03:00"`, `interval_hours` 6, `action` `"run_all"`). The pure `compute_next_fire(now, cfg, last_fire)` computes the next datetime (daily: today at `time` if future else tomorrow; interval: `(last_fire or now) + interval_hours`; bad `time` → midnight) and is unit-testable with no clock access. The thread loops in ≤30 s slices (so config/stop changes apply fast) and calls `_fire_if_due(now, due)`, which **skips** (emitting a gray 「排程時間到，但已有任務執行中，略過本次」 log) when `is_active()` reports a run in progress. It is decoupled via three callables (`get_cfg` / `run_all` / `is_active`, plus optional `emit`). `flet_app.main` starts it (after `run_controller` is built) only when `schedule.enabled` — `run_all` → `run_controller.run_all()`, `is_active` → live thread aliveness on `main_view._active_thread`, `emit` → `event_q.put(WorkerEvent("output", html))` — and registers `scheduler_service.stop` via `atexit`; the settings 「排程」tile binds the section.

Tests: `tests/test_scheduler_next_fire_time.py`, `test_scheduler_skips_when_active.py`, `test_headless_runner_runs_step.py`, `test_headless_run_all_chaining.py`, `test_cli_status_json.py`, `test_cli_config_get_set.py`.

### Cookie pool

The app supports multiple cookies in rotation. The cookies view owns `cookies_pool` (list[str]) and `_cookie_alias_map` / `_cookie_status_map`. Entries are normalized (strip `Cookie:` prefix, dedupe) before being stored and passed to threads; downloader threads record per-PID cookie usage in `_pid_cookie_used`, which takes priority over `url_meta[pid].requires_cookie` (see `tests/test_cookie_cooldown.py`).

### Per-account cooldown + proxy binding (Steps 2/3/4)

`AccountScheduler` (`app/core/account_scheduler.py`) is a single-consumer round-robin state machine that gates HTTP work behind a per-account cooldown. Each `AccountState` holds `(cookie, alias, proxy_url, cooldown_until, disabled_reason)`; `proxies` property returns the `requests`-compatible dict via `app/core/proxy_utils.to_requests_proxies`. Workers in Steps 2/3/4 call `_acquire_account()` (blocks until next available) before each work unit and `_release_account(acc, ok=...)` after; `ok=False` (raised by `ProxyError` / `ConnectionError`) marks that cookie disabled for the entire run.

On a `(ProxyError, ConnectTimeout, ConnectionError)` raised inside the four scheduler-aware call sites (Steps 2/3/4), the worker retries on the **same account** up to **5 attempts total** with a fixed **60 s** wait between attempts (constants `NETWORK_RETRY_ATTEMPTS` and `NETWORK_RETRY_WAIT_SEC` in `app/core/pixiv_thread_base.py`). The retry is implemented in `PauseableThread._run_with_network_retry`. Only after all 5 attempts fail does the cookie get disabled via `release(ok=False)`. The 60 s wait is interruptible by `stop_event` and skipped during pause (paused time does not count toward the budget).

Settings keys driving this:
- `performance.pid_cooldown_avg` — single live-adjustable value (slider in settings UI). Per-account cooldown is the deterministic `avg × ln(N+1)` seconds (no jitter on this), where N is active account count. Randomness lives on a throughput gate inside `acquire()`: each successful pickup advances `next_emit_at` by `throughput × random(0.9, 1.1)` where `throughput = avg × ln(N+1) / N`. This bounds the inter-request gap to ±10% of throughput regardless of N, instead of the unbounded variance you get from per-account jitter. Initial per-account cooldowns are staggered by one throughput interval so the first round paces correctly and the UI countdown shows "next request in X seconds". `AccountScheduler.average_cooldown()` returns the throughput. The settings UI warns when `avg < 30`.
- `auth.proxy_pool: list[str]` — multi-line proxy list edited in the settings "Proxy 設定" tile (`http://`, `https://`, `socks5://` URLs accepted; auto-detected via scheme).
- `auth.cookie_proxy_map: dict[cookie_str, proxy_url | None]` — bound in the cookies view's "Proxy 綁定" dropdown column. `None` = use local IP. Same account always uses same IP (hard contract).

`RunController._build_scheduler` (`app/gui/run_actions.py`) wires the scheduler from settings before launching n=2/3/4 threads; n=1 (`thread_following`) does not use a scheduler. The scheduler reads `pid_cooldown_avg` live (lambda over `_store()`) so a UI slider change takes effect on the next `release()`.

`pixiv_api.make_session(proxy_url)` builds a `requests.Session` with the bound proxy; `Pixiv_info(..., session=...)` keyword-only arg routes traffic through it. Step 2 (`thread_pid_scan`) passes `proxies=acc.proxies` directly to `requests.get`; Step 4 (`thread_download`) shares one session per PID across all multi-page downloads. ProxyError must propagate to the worker's release boundary — `Pixiv_info`, `gif_download`, `jpg_download`, `get_download_url`, and `thread_no_use_seleium_get_pid` all re-raise `(ProxyError, ConnectTimeout, ConnectionError)` before their broad `except Exception` handlers.

Deprecated: `cookie_speed_divisor` and `apply_cookie_pool_speedup` in `pixiv_thread_utils.py` are superseded by `AccountScheduler` and kept only for import compat.

## Conventions worth knowing

- User-facing strings and log/output messages are Traditional Chinese; keep that style when touching UI.
- Workers extend `PauseableThread` (which extends `threading.Thread`); never call network code on the GUI thread. Workers push `WorkerEvent(type=..., data=...)` (field is `type`, not `kind`) onto the shared `queue.Queue`; `EventDispatcher` routes them to the correct handler on the Flet event loop.
- Writes to shared state files go through `safe_io.atomic_write_*` or `pixiv_thread_utils.atomic_write_*`, not raw `open(..., "w")`, to survive interrupted runs.
