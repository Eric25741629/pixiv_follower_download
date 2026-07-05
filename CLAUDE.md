# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Before touching `app/gui/`, `app/core/thread_*.py`, or anything Flet-related,
> read `.claude/skills/flet-0-84-pitfalls/SKILL.md`.** It documents every API
> rename, threading hazard, and dialog-system change that bit during the
> PyQt5 → Flet 0.84 migration.
>
> **Before touching `app/core/thread_combined.py`, any reuse of engine methods
> outside their run(), SettingsStore write paths, or worker progress/stop
> handling, read `.claude/skills/combined-compose-pitfalls/SKILL.md`** — the
> distilled repeat-offender bugs (uninitialised composed-engine state, shared
> queue multi-sender pollution, stop≠success, settings RMW races).
>
> **Code changes must be made in an isolated git worktree** (EnterWorktree /
> `git worktree add`), never directly on the main working tree — it routinely
> carries the user's uncommitted WIP, and editing in place mixes your diff
> with theirs (unreviewable, uncommittable per-file). Merge back when green.

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

  **Mixin split (file-size refactor).** Several oversized worker/DB modules were split into cohesive sibling modules using the mixin pattern — the public class still lives in (and is imported from) its original module, which declares `class X(BaseFirst, _MixinA, _MixinB, ...)`; moved methods access state via `self.` through inheritance. Edit the sibling, not a copy. Map:
  - `thread_download.py` → `step4_author_order.py` (pure reorder funcs, re-exported), `step4_filters.py` (`_Step4FiltersMixin`), `step4_media.py` (`_Step4MediaMixin`), `step4_legacy_args.py` (`_Step4LegacyArgsMixin`), `step4_pacing.py` (`_Step4PacingMixin`), `step4_folder_list.py` (`_Step4FolderListMixin`), `step4_db_sync.py` (`_Step4DbSyncMixin`), `step4_init.py` (`_Step4InitMixin`: 建構期 helpers；`defer_step4_scan` 分支本體仍在 `__init__`); also the pre-existing `_FilenameMixin` / `_JXLMixin`.
  - `thread_url_fetch.py` → `step3_filters.py` (`_Step3FiltersMixin`), `step3_meta_migration.py` (`_Step3MigrationMixin`), `step3_persistence.py` (`_Step3PersistenceMixin`).
  - `thread_pid_scan.py` → `step2_bookmark_scan.py` (`_Step2BookmarkMixin`), `step2_incremental_io.py` (`_Step2IncrementalIOMixin`).
  - `metadata_db.py` → `metadata_db_pages.py` (`_PagesMixin`), `metadata_db_closed_set.py` (`_ClosedSetMixin`); also the pre-existing `_ArtworkMixin` / `_MigrationMixin`.
  - `pixiv_api.py` → `pixiv_selenium_login.py` (selenium login surface), `pixiv_legacy_utils.py` (shadowed legacy free-funcs); both star-re-exported back so `from pixiv_api import *` stays byte-identical.
  - `pixiv_thread_utils.py` → `folder_scan.py`, `json_recovery.py` (re-exported facade).
  - `event_log.py` → `event_log_io.py` (pure reverse/forward file iteration, re-exported).
  - `thread_combined.py` → `combined_progress_queues.py` (the two `_Combined*`/`_Drop*` event-queue adapters, re-exported), `combined_work_lists.py` (`_CombinedWorkListsMixin`: `_build_work_lists` / `_resolve_combined_order` / `_download_only_urls`). `_process_one_pid` / `run` stay in `thread_combined.py`.
  - GUI: `views/main_view.py` → `views/main_progress.py` (`_MainProgressMixin`), `views/main_mode_row.py` (`_MainModeRowMixin`); `run_actions.py` → `cookie_validation.py` (`_CookieValidationMixin`); `views/settings_view.py` → `views/settings_handlers.py` (`_SettingsHandlersMixin`); `flet_app.py` → `bootstrap_helpers.py` (stateless module-level helpers only — the global-mutating `main(page)` closures stay put).

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

**Startup-cost note (`closed_artwork_set`).** This is the hottest call in the init path — on a real 1.26M-row DB it returns ~1.1M PIDs and is invoked 5-6× per Run All (`_build_step2/3`, `_build_combined`, the folder-sync DB augment, `download_thread._load_initial_exist_pid_set`, and `downloaded_count` inside `emit_db_stats`). Two optimisations keep it cheap: (1) it is composed in Python — `(sentinels − pending) | complete | revoked` via four indexed single-column SELECTs — instead of the `v_closed_artworks` SQL `UNION` (which spooled ~1.1M rows into a TEMP B-TREE: ~23s → ~7s, byte-identical result); (2) results are memoised in a **process-global cache keyed by a cheap DB file signature** (`size + mtime_ns` of `metadata.sqlite3` + its `-wal`), so repeat calls while the DB is unchanged cost ~50ms. Any committed write grows the WAL → signature changes → automatic recompute (no manual invalidation). `downloaded_count()` routes through this cache (`len(closed_artwork_set())`), and the redundant `_mirror_exist_pid_to_db()` re-import of the DB-sourced set was removed from `download_thread` / `thread_pid_scan` init (it only re-scanned ~1.1M rows and invalidated the cache). The `v_closed_artworks` view still exists for `is_pid_closed` / ad-hoc queries.

**Legacy tables (`pids`, `downloaded`, `pending_urls`, `pending_pids`) were dropped in Phase 8.** `_SCHEMA` runs `DROP TABLE IF EXISTS` on every open (`app/core/metadata_db.py:38-41`) and nothing writes to them — the `MetadataDB` methods named after them (`upsert_pending_urls`, `upsert_pending_pids`, `mark_url_done`/`mark_urls_done`) write the canonical `pages` / `artworks` tables instead, so legacy-table write-amplification is zero. (This is separate from the still-active PHASE-A `exist_pid` shadow-write into `artworks` sentinel rows described below.) JSON / text files `exist_pid.json`, `all_url.txt`, `pictures_id.txt`, `err_url.txt` likewise persist for compatibility but the canonical state lives in SQLite.

#### `exist_pid` DB-only migration (in progress — search `PHASE-A`)

Phase A (current): `sync_exist_pid_with_download_folder` writes scanned PIDs to BOTH `exist_pid.json` AND `metadata.sqlite3` via `_shadow_write_exist_pid_to_db` → `mirror_exist_pid_set` → sentinel rows in `artworks` that `v_closed_artworks` picks up. Steps 2/3 still read `load_exist_pid_set(path)` (JSON ∪ DB). Step 4 already uses `db.closed_artwork_set()` directly.

**Folder-scan cache.** The download-folder walk is the other big init cost (on the real folder: 204k files, ~10s). `sync_exist_pid_with_download_folder` records each visited directory's `st_mtime_ns` in `folder_file_count_cache.json` (`dir_mtimes`); a later run re-stats just those directories (`_folder_dir_mtimes_match`, O(#dirs)) and **skips the `os.walk` entirely** when none changed (~0.3ms vs ~10s). Adding/removing/renaming any file bumps its parent dir's mtime, and creating a sub-dir bumps *its* parent's mtime, so this catches every change. On a miss it does a **single** walk (`_scan_download_folder` returns pids + dir_mtimes + count in one pass, replacing the old count-walk-then-scan-walk) and shadow-writes only the **newly-discovered PID delta** (not the whole folder set), which also keeps the closed-set file signature stable across the step build.

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
- `artwork.user_id_backfill` — `backfill_user_ids` (UPDATE-only author fill for existing rows; Step 2 full-artist `user_id` backfill — see Author-ordered downloads)
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

The **same `download.author_order` switch also drives combined mode and Step 2** (so one toggle groups download order in Step 4, combined mode, and the physical `pictures_id.txt` order):
- **Combined mode**: `combined_thread._resolve_combined_order(query_pids, download_only)` reuses `compute_author_order` to reorder the merged work list (`author_order` off → `[(p,True) for query] + [(p,False) for download_only]`, byte-identical to before; on → both batches merged, deduped by `normalize_pid` with the query batch winning, grouped by author, unknown bucket last). combined is per-PID sequential (one account per PID) so the flat reorder alone yields "same author contiguous" — no pool barrier needed. `run()` resolves the order once and derives the progress denominator from it. Test: `tests/test_combined_author_order.py`.
- **Step 2**: `get_pixiv_author_imgID_Thread.__init__(..., author_order=...)` (wired from `run_actions._build_step2` reading `download.author_order`); at the end of `run()`, `_commit_step2_outputs` calls `_regroup_pictures_id_by_author()` which rewrites `pictures_id.txt` author-grouped via `compute_author_order` + `atomic_write_text(..., backup=True)` (history-backed). Incremental flushes stay append-only; only this final pass regroups. Skips silently when DB/uid_map unavailable, file empty, or already grouped. Tests: `tests/test_step2_regroup_pictures_id.py`, `test_step2_author_order_wiring.py`.
- **Step 2 full-artist `user_id` backfill** (so grouping works for large pre-existing pending sets without re-querying each PID): each artist scan already fetches the artist's *full* PID list; `_step2_backfill_author_user_ids(kept + truncated, author)` (called per artist in `thread_no_use_seleium_get_pid`, gated behind `author_order`, serialised by `_step2_db_write_lock` since the connection has no `busy_timeout`) calls `MetadataDB.backfill_user_ids` — an **UPDATE-only** author fill (`UPDATE artworks SET user_id=? WHERE pid=? AND (user_id IS NULL OR '')`, first-writer-wins, **never inserts** so it can't add `v_pending_artworks` rows or disturb the truncation/queue). This fills the author even for older PIDs the incremental scan truncated, so the subsequent `_regroup_pictures_id_by_author` (and combined/Step-4 ordering) group them. Emits the `artwork.user_id_backfill` event for replay. Tests: `tests/test_metadata_backfill_user_ids.py`, `test_step2_user_id_backfill.py`.

### Combined fetch-download (邊查邊下)

Optional `download.combined_mode` (bool, default false; UI switch 「邊查邊下（查到即下載，合併步驟三、四）」 in settings) merges Step 3 (query meta) and Step 4 (download) into a single per-PID pass so that resolving a PID's meta and downloading its pages both happen inside **one account cooldown window** instead of two separate full-pipeline passes. The new thin orchestrator `combined_thread(PauseableThread)` in `app/core/thread_combined.py` **composes** a `get_img_url_thread` (query engine, `self.fetcher`) and a `download_thread` (download engine, `self.downloader`) as helpers — it never calls their `run()`. The two engines share one event queue, one `AccountScheduler` (propagated post-construction via `_share_scheduler`), one `_pause_event` / `_stop_event`, and one `_metadata_db` connection (downloader's DB is reassigned to the fetcher's).

The work queue is built by `_build_work_lists()` → `(query_pids, download_only_pids)`. `query_pids` come from `pictures_id.txt` via the fetcher's pure filter helpers (`check_exist` + `_prepare_pending_pid_tasks`, deliberately **not** `_load_and_filter_pid_list`, to avoid its next/progress emits; it also seeds the fetcher's pending-PID tracker so finalize doesn't blank `pictures_id.txt`). `download_only_pids` are PIDs with pending pages in the DB (`MetadataDB.pids_with_pending_pages()` reading `v_pending_pages`) that are **not** already in `query_pids` — this auto-absorbs a partial Step 3 that resolved meta but never downloaded. Their per-page URLs are grouped once from `get_pending_pages()` into `_pending_urls_by_pid` (read via `_download_only_urls`), avoiding an O(D×P) re-scan per PID.

`_process_one_pid(pid, needs_query)` acquires one account, runs the query (via `_run_with_network_retry` + `fetcher.get_download_url`) only when `needs_query`, **seeds the canonical `pages` rows** for the resolved URLs (`db.upsert_pending_urls`) before downloading them through `downloader._download_pid_group` — also wrapped in `_run_with_network_retry`, so a proxy error on the download leg retries (and disables the cookie via `release(ok=False)`) instead of aborting the whole run. On a clean download it marks them done (`db.mark_urls_done`), flushes the closed sentinel (`_maybe_flush_exist_pid`), and **persists that PID's meta immediately** (`_persist_pid_meta` → `import_meta_dict`) so `page_count` + `meta_updated_at` land at once and `v_complete_artworks` / `v_closed_artworks` are exact and crash-safe. `ok` / `download_ok` are initialised before the `try` so the `finally` `_release_account(ok=ok and download_ok)` is always valid; `_mark_pid_processed` runs only on genuine per-PID success, so an exhausted query or partial-download failure stays pending for the next run. `run()` emits 「邊查邊下階段開始」, iterates `_resolve_combined_order(query_pids, download_only)` (author-grouped when `download.author_order` is on — see the Author-ordered section), finalizes — fetcher: `_flush_url_meta_snapshot` / `_persist_pending_pid_file` / `_flush_revoked_pid_file`; downloader: **failure recording only** (`_classify_download_results` → `err_url.txt` + `_shadow_mark_failures`), deliberately **not** `_finalize_downloads` (which would clobber `all_url.txt` from the now-empty `allurl`) — and emits a **terminal `WorkerEvent("next", -1)`** so `Run All` stops after combined mode rather than chaining into a separate Step 4.

The downloader is constructed with the opt-in `defer_step4_scan=True` + `db_base_path` (new keyword-only `download_thread.__init__` params; default off = byte-identical Step 4). Under `defer_step4_scan` the constructor skips the Step-4-only heavy init (`_load_initial_exist_pid_set` / `_mirror_exist_pid_to_db` / `_emit_metadata_db_stats` / `_warn_if_meta_empty_with_like_filter` / `_read_all_url_file_into_state` / `_prepare_download_tasks`, setting safe defaults `exist_pid=set()` / `allurl=[]` / `pid_max=0`) that otherwise scans the full metadata DB at construction — a ~40 s freeze on a large DB. `db_base_path` opens the metadata DB at the combined base path so the downloader shares the fetcher's DB from the start.

Routed in `RunController._build_thread(3)`: when `download.combined_mode` (in `DEFAULTS["download"]`, default False) is on **or** `RunController.force_combined` is set (the CLI `run --step combined` path), `_build_combined` (mirrors the `_build_step3` + `_build_step4` arg assembly) is returned instead of `_build_step3`; off → unchanged Step 3 (zero regression). Tests: `tests/test_metadata_pids_with_pending.py`, `test_combined_mode_wiring.py`, `test_combined_work_queue_absorbs_pending.py`, `test_combined_one_cooldown_per_pid.py`, `test_combined_cache_hit_no_network.py`, `test_combined_run_all_terminal_next.py`, `test_combined_query_seeds_and_marks_db.py`.

**Parallel combined (`download.combined_workers`, int, default 1 = sequential, zero regression; settings 「邊查邊下並發數」 field).** When `combined_workers > 1`, `run()` computes effective K via `resolve_worker_count(setting, active_cookies, pending, cap=16)` and calls `_run_concurrent` instead of `_run_sequential`. `_run_concurrent` is a single **coordinator** (the run thread) driving a `ThreadPoolExecutor(max_workers=K)` with sliding-window submission (`concurrent.futures.wait(FIRST_COMPLETED)`): each worker queries+downloads one PID on its **own** account (the `AccountScheduler.held` flag guarantees distinct cookies; `acquire`/`release` are lock-safe). This is as safe as Step 4's existing pool mode — `download_thread`'s current-account is `threading.local`, `MetadataDB` connections are `threading.local`, `_pid_cookie_selection`/`url_meta` key per-PID. The **freeze** that killed the reverted Phase 1 is avoided by making the coordinator the *sole* event producer: workers emit nothing because both engines' `_q` are swapped once to `_DropProgressQueue` (drops `progress`/`page_progress`) for the whole phase and restored in a `finally` after the pool joins; the coordinator owns overall progress (one tick per finished PID), the pending-tracker retire, and a lightweight aggregate phase line (`邊查邊下中（n/K 並發，完成 d/total）：PID …`). (Timetag persistence is no longer per-PID — it is done once up front by `assign_pid_timetags`; see the Download-timetag section.) `_process_one_pid` is now a thin wrapper over `_process_one_pid_core(..., *, emit_phase, page_progress, drop_overall_inline, apply_live) -> (failed, ok)`; the sequential wrapper passes all the legacy flags so K=1 is byte-identical. **Each PID's pages share ONE timestamp** (see the Download-timetag section): the per-PID block is owned by `download_thread._download_pid_group` (the common download choke point that every mode funnels through), so concurrent PIDs read disjoint **pre-allocated** stamps (assigned up front by `assign_pid_timetags`, an O(1) map lookup with no lock on the hot path) — combined mode no longer manages the block itself. `RunController._build_combined` reads `download.combined_workers`. Tests: `tests/test_combined_parallel.py`.

**Anonymous probe prefetch (acquire 前匿名探測).** Before acquiring an account for a `needs_query` PID, combined probes the artwork meta **anonymously** (local IP, no cookie, no proxy) via `combined_thread._probe_pid_anonymously` — capped at `PROBE_MAX_CONCURRENCY = 8` concurrent probes (a `BoundedSemaphore`), deliberately **no other throttle** (user decision 2026-07-05). The raw `Pixiv_info` result is stashed on the fetcher (`_probe_info_results`, keyed per PID) and consumed one-shot by `_step3_fetch_artwork_info`, so the follow-up `get_download_url` (invoked accountless with `cookie_override=""`) resolves meta **without a second network hit**. Outcomes: PIDs the probe shows as filtered / 404 / fully-on-disk **settle with zero account cost** (never acquire); PIDs with pages to download enter the account section carrying `prefetched_urls` and release with **`work_units=0`** (cooldown counts pages only — `_mark_success_locked` now accepts 0, and the 1-3 s query→download buffer is skipped since the pickup's first request IS the download); anonymous-blocked (R18) PIDs are recorded in `fetcher._probe_requires_cookie` so the account query calls `Pixiv_info(..., skip_no_cookie=True)` and does not repeat the doomed anonymous first fetch. Safety rails: `self._scheduler is None` → probe disabled (keeps stub-based unit tests offline — a test that installs a REAL `AccountScheduler` and drives `_process_one_pid` must stub `_probe_pid_anonymously` to `None`); probe exception / `["error"]` → legacy account-query path; stop during the accountless query → fallback to acquire (which returns None) so a stop can **never** settle an unprocessed PID. Tests: `tests/test_combined_anon_probe.py`.

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

### Download timetag persistence (`download.download_time`)

Step 4 stamps files with a timetag, embedded in the filename prefix and (when `download.set_file_mtime`, default true) applied to the file's atime/mtime via `_apply_download_mtime`. **The unit is the PID, not the file: every page of one artwork shares ONE timestamp** (so an artwork's pages get the same prefix / mtime and group as a unit). **Stamps are pre-allocated, not reserved lazily under a lock.** Once the iteration order is known, `assign_pid_timetags(pid_order)` builds `self._pid_timetag = {str(pid): base + i s}` (one stamp per PID by queue position) and advances `self.download_time` past the whole block, persisting it **once** via a single `_emit_timechanged()`. Called from Step 4 `run()` (after `_resolve_execution_order`, before `_execute_downloads`) and combined `run()` (after `_resolve_combined_order`, on `self.downloader`). `_download_pid_group` — the single download choke point for every Step 4 path (single / pool / scheduler) **and** combined mode — calls `_begin_pid_timetag_block(pid)` at the start of a PID's pages (an **O(1) map lookup, no lock**; a requeued/never-assigned straggler falls back to the legacy lazy +1 s reserve under `timelock`) and `_end_pid_timetag_block()` in `finally`; `_reserve_one_timetag()` returns that `base` for every page while a block is active (no per-page advance). Distinct PIDs therefore get distinct stamps and concurrent PIDs read disjoint stamps with no contention; **uniqueness of each file is carried by `PID{pid}{page_suffix}`, not the timetag, so a shared (or even repeated) stamp is collision-safe**. `flet_app.handle_timechanged` writes the persisted value back to `download.download_time`. Because the cursor is persisted up front, a crash mid-run can never reuse stamps; a partial run leaves the unused tail seconds as harmless gaps (the timetag is only a sort key). `_apply_live_settings_if_changed` deliberately does **not** re-apply `download_time` mid-run: the run's stamps are immutable (pre-allocated in the map) so a live edit would be meaningless, and re-applying it would rewind the cursor to the stale settings value (the up-front emit advances ahead of the GUI's write-back). A genuine user edit to 「下載時間戳起點」 is picked up at the next run's construction. The settings view exposes 「下載時間戳起點」 and the mtime switch. (`tools/fix_duplicate_timetags.py` repairs the old cross-PID-collision corruption **PID-aware**: it de-dupes by PID, never splitting one PID's pages apart.) Tests: `tests/test_per_pid_shared_timetag.py`, `tests/test_fix_duplicate_timetags.py`.

### Per-account cooldown + proxy binding (Steps 2/3/4)

`AccountScheduler` (`app/core/account_scheduler.py`) is a multi-consumer-safe state machine that gates HTTP work behind a per-account cooldown. Among the accounts that are ready (off cooldown, not held), it picks one by **idle-weighted random selection** (`_pick_weighted_by_idle`: weight = `(now - cooldown_until)` idle seconds + one throughput interval as a base), so load spreads in a balanced spread around the mean and a long-neglected account keeps gaining weight until it wins — no account starves. This replaced the original `available[0]` fixed-list-order pick, which under any demand slack let the front of the pool satisfy every request and left the tail accounts at zero (the cause of the steep 「Cookie 請求次數」 skew + never-selected cookies). `acquire()` marks the returned account `held` so concurrent pool workers can never check out the same account; `release()` / `release_neutral()` clear it. `release_neutral` (no disable, no first-success credit, normal cooldown) is used when the unit of work was aborted by user stop or failed for a non-network reason — only genuine retry exhaustion on the network triple disables a cookie. The 「所有 Cookie 都已禁用」 message is emitted once per run; non-zero countdown ticks are throttled to 1/s across workers. Each `AccountState` holds `(cookie, alias, proxy_url, cooldown_until, disabled_reason, held)`; `proxies` property returns the `requests`-compatible dict via `app/core/proxy_utils.to_requests_proxies`. Workers in Steps 2/3/4 call `_acquire_account()` (blocks until next available) before each work unit and `_release_account(acc, ok=...)` after; `ok=False` (raised by `ProxyError` / `ConnectionError`) marks that cookie disabled for the entire run.

On a `(ProxyError, ConnectTimeout, ConnectionError)` raised inside the four scheduler-aware call sites (Steps 2/3/4), the worker retries on the **same account** up to **5 attempts total** with a fixed **60 s** wait between attempts (constants `NETWORK_RETRY_ATTEMPTS` and `NETWORK_RETRY_WAIT_SEC` in `app/core/pixiv_thread_base.py`). The retry is implemented in `PauseableThread._run_with_network_retry`. Only after all 5 attempts fail does the cookie get disabled via `release(ok=False)`. The 60 s wait is interruptible by `stop_event` and skipped during pause (paused time does not count toward the budget).

#### Per-page download deadline (the 2026-06-21 trickle-wedge fix)

`requests`' `timeout` with `stream=True` is a **per-recv** socket deadline only — urllib3 never sets `Timeout.total` from requests, so a half-open/trickling connection (a few bytes inside every read-timeout window) keeps `iter_content` looping forever **without raising**. Because the exception-only retry loop in `jpg_download` never fires on a no-exception hang, a single trickling image used to wedge the single sequential combined worker indefinitely → Flet 0.84 GC'd the idle session → the client reverted to the built-in "Working…" splash → the window-close handler (bound to the dead session) never fired → an unkillable orphan process. Two layers now bound every per-page download:

- **Per-recv read timeout** (`DOWNLOAD_CONNECT_TIMEOUT=10`, `DOWNLOAD_READ_TIMEOUT=30` in `pixiv_thread_base.py`) on the request, bounding a *fully-silent* socket. `_jpg_attempt`, `_stream_ugoira_zip_bytes`, and `fetch_with_cookie_retry` (the ugoira_meta fetch — previously the only request with **no** timeout) all pass the `(connect, read)` tuple.
- **Total wall-clock deadline** (`performance.download_deadline_sec`, default 120; `download_thread._download_deadline_sec`) enforced in Python between chunks by `PauseableThread._stream_to_sink(response, write, *, chunk_size, deadline_sec)`, which bounds a *trickle* and re-checks `stop_event` every chunk (paused time is refunded, mirroring `_wait_interruptible`), and `response.close()`s on every exit path. Both `_jpg_stream_to_disk` (which streams to `filepath + '.part'` then `os.replace`s — so an aborted transfer never leaves a truncated file under the final name that the folder-scan would treat as already-downloaded) and `_stream_ugoira_zip_bytes` (bytearray sink) drain through it.

Settlement contract: `_stream_to_sink` raises `DownloadDeadlineExceeded` (a deadline = page failure: `jpg_download`/`gif_download` catch it and return the fail-list `[url, timetag]` **without retry and without disabling the cookie** — the PID stays pending, retried next run) or `DownloadStopped` (a user Stop = NOT a failure: it propagates to `gif_or_jpg`, which requeues the URL and returns the `0` sentinel **without** `_record_completed`, leaving the page pending — no err_url, no `attempt_count` bump). Neither exception is in the network-retry triple, so a deadline can **never** escape `jpg_download`/`gif_download` into combined's `_run_with_network_retry` and abort the whole run. Wired through `_LEGACY_SCALAR_KW_SCHEMA` → `download_thread.__init__` and forwarded by `combined_thread.__init__`; both `run_actions._build_step4` / `_build_combined` read `performance.download_deadline_sec`. Tests: `tests/test_download_deadline.py`, `test_combined_download_deadline_integration.py`. (Known residual: the meta GETs `Pixiv_info` and the Step 1/2 scans still have per-recv-only timeouts and no total deadline — small-JSON bodies make the trickle window negligible, but they are not wall-clock-bounded.)

**Shutdown watchdog (always-closeable backstop).** `flet_app._arm_shutdown_watchdog(timeout=8, reason)` starts (once) a bare daemon thread that `os._exit(0)`s after the deadline; `_shutdown_and_destroy` arms it **first**, then runs the graceful flush/checkpoint/destroy whose own `os._exit(0)` wins the race on a healthy disk. It imports nothing from flet and joins nothing, so a worker wedged in a non-interruptible C syscall (or a flush that hangs) can't prevent exit. `_install_signal_handlers()` (called from `main()`, kept **separate** from `_install_crash_hooks` so unit tests don't leak a process-global handler) registers `SIGINT`/`SIGBREAK` → flush + arm + `os._exit(0)`, the out-of-band kill channel for a dead post-GC "Working…" window whose X no longer reaches Python. Tests: `tests/test_shutdown_watchdog.py`.

Settings keys driving this:
- `performance.pid_cooldown_avg` — single live-adjustable value (slider + number field in settings UI, range 0-300). Per-account cooldown is the deterministic setting value itself: each account rests exactly `avg` seconds after a unit of work, **independent of account count** (the `× ln(N+1)` scaling was removed 2026-06 at user request — the dial now reads as "my single account's rest time"). Randomness lives on a throughput gate inside `acquire()`: each successful pickup advances `next_emit_at` by `throughput × random(0.9, 1.1)` where `throughput = avg / N` (N = active account count). This bounds the inter-request gap to ±10% of throughput. `avg = 0` is allowed: per-account floor is 1 s (`max(1.0, avg)`) and the gate floor is 0.5 s. Initial per-account cooldowns are staggered by one throughput interval so the first round paces correctly and the UI countdown shows "next request in X seconds". `AccountScheduler.average_cooldown()` returns the throughput. The settings UI warns when `avg < 30`.
- `auth.proxy_pool: list[str]` — multi-line proxy list edited in the settings "Proxy 設定" tile (`http://`, `https://`, `socks5://` URLs accepted; auto-detected via scheme).
- `auth.cookie_proxy_map: dict[cookie_str, proxy_url | None]` — bound in the cookies view's "Proxy 綁定" dropdown column. `None` = use local IP. Same account always uses same IP (hard contract).

`RunController._build_scheduler` (`app/gui/run_actions.py`) wires the scheduler from settings before launching n=2/3/4 threads; n=1 (`thread_following`) does not use a scheduler. The scheduler reads `pid_cooldown_avg` live (lambda over `_store()`) so a UI slider change takes effect on the next `release()`.

`pixiv_api.make_session(proxy_url)` builds a `requests.Session` with the bound proxy; `Pixiv_info(..., session=...)` keyword-only arg routes traffic through it. Step 2 (`thread_pid_scan`) passes `proxies=acc.proxies` directly to `requests.get`; Step 4 (`thread_download`) shares one session per PID across all multi-page downloads. ProxyError must propagate to the worker's release boundary — `Pixiv_info`, `gif_download`, `jpg_download`, `get_download_url`, and `thread_no_use_seleium_get_pid` all re-raise `(ProxyError, ConnectTimeout, ConnectionError)` before their broad `except Exception` handlers.


## UI design system (`app/gui/components`)

All Flet UI is built from the **`app/gui/components`** factory library (themed on top of `app/gui/glass.py`'s `GlassTheme` tokens + `glass_panel` / `glass_dialog`), so styling and behavior live in one place instead of being hand-rolled per view. Import as `from app.gui import components as c`. Layers: `inputs` (`number_field`, `text_field`, `multiline_field`, `dropdown`, `switch`, `slider` — each bakes in the border/focus/active/inactive glass styling that views used to retrofit via `_apply_input_theme`), `layout` (`page_title`, `subhead`, `note`, `status_note`, `inline_label`, `section` — the unified `_tile`), `buttons` (`primary_button`, `secondary_button`, `icon_action`), `dialogs` (`confirm_dialog`). Factories take the `GlassTheme` (from `current_theme(page)`) first where they style.

**New UI must route through this library, not raw `ft.*` themed controls.** The guardrail `tests/test_ui_no_raw_controls.py` fails if any view file constructs a raw `ft.TextField/Switch/Slider/Dropdown/FilledButton/OutlinedButton` (it passes with zero allowlist today). Pure layout (`ft.Row/Column/Container`), `ft.Text` display cells, `ft.Checkbox`, and the custom `glass_pill` action buttons stay raw. Note: Flet 0.84 `ft.Dropdown`'s change handler is **`on_select`**, not `on_change`. To add UI: extend the library (factory + test in `tests/test_ui_components.py` + `__init__.py` re-export), then use it. When reviewing/writing UI, use the **`ui-design-system-review`** skill. Tests: `tests/test_ui_components.py`, `tests/test_ui_no_raw_controls.py`.

## Conventions worth knowing

- User-facing strings and log/output messages are Traditional Chinese; keep that style when touching UI.
- All UI is built from the `app/gui/components` design-system factories, not raw `ft.*` themed controls (see the UI design system section; guardrail `tests/test_ui_no_raw_controls.py`).
- Workers extend `PauseableThread` (which extends `threading.Thread`); never call network code on the GUI thread. Workers push `WorkerEvent(type=..., data=...)` (field is `type`, not `kind`) onto the shared `queue.Queue`; `EventDispatcher` routes them to the correct handler on the Flet event loop.
- Writes to shared state files go through `safe_io.atomic_write_*` or `pixiv_thread_utils.atomic_write_*`, not raw `open(..., "w")`, to survive interrupted runs.
