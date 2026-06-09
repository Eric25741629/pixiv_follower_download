from __future__ import annotations
import atexit
import os
import queue
import sqlite3
import sys
import threading
import time
from collections import deque
import flet as ft

from app.core.app_logging import init_logging, get_logger
from app.gui.dispatcher import EventDispatcher
from app.gui.run_actions import RunController
from app.gui.views.main_view import MainView
from app.gui.views.settings_view import SettingsView
from app.gui.views.cookies_view import CookiesView
from app.gui.views.stats_view import StatsView
from app.core.stats_collector import StatsCollector
from app.core.settings_store import SettingsStore
from app.core.worker_event import WorkerEvent

_log = get_logger("pixiv.gui")


def _settings_store() -> SettingsStore:
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return SettingsStore(path)


def _event_log_kwargs() -> dict:
    """Read othersettings.event_log durability/rotation knobs for EventLog.

    Defaults batch the fsync (200 events / 1s) so the common write path no
    longer pays a per-mutation disk barrier; anchor kinds and close() still
    fsync. Missing/invalid values fall back to the defaults.
    """
    try:
        cfg = _settings_store().get_section("event_log")
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    def _i(key, default):
        try:
            return int(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _f(key, default):
        try:
            return float(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    return {
        "retention_days": _i("retention_days", 60),
        "fsync_every_n": _i("fsync_every_n", 200),
        "fsync_interval_sec": _f("fsync_interval_sec", 1.0),
        "max_total_bytes": _i("max_total_bytes", 4 * 1024 * 1024 * 1024),
        "rotate_size_bytes": _i("rotate_size_bytes", 128 * 1024 * 1024),
    }


def _load_theme_mode() -> ft.ThemeMode:
    try:
        name = _settings_store().get_section("ui").get("theme_mode", "SYSTEM")
    except Exception:
        return ft.ThemeMode.SYSTEM
    if name == "DARK":
        return ft.ThemeMode.DARK
    if name == "LIGHT":
        return ft.ThemeMode.LIGHT
    return ft.ThemeMode.SYSTEM


def _save_theme_mode(mode: ft.ThemeMode) -> None:
    try:
        _settings_store().update_fields("ui", {"theme_mode": mode.name})
    except Exception:
        _log.exception("failed to persist theme_mode")

# ── cross-session persistence ──────────────────────────────────────────────
# Flet 0.84 desktop GCs the server-side session if the flutter client
# disconnects/reconnects — observed when (a) the window is blurred for
# several minutes, or (b) rapid nav-rail clicks block the event loop with
# sync file I/O long enough for the flutter client to drop the TCP socket
# and reopen it. When that happens Flet calls main(page) again with a
# fresh page; without persistence, the new main() would build a brand-new
# event_q and the running worker thread (which still holds a ref to the
# OLD event_q) would be orphaned — its events flow to a queue nobody
# polls, and the new UI shows the app reset to idle.
#
# Keep these alive across main() calls so a session GC doesn't kill a
# running download. The worker thread keeps the same Queue object; the
# new dispatcher picks up where the old one left off.
_PERSISTENT_EVENT_Q: queue.Queue = queue.Queue()
_PERSISTENT_STATS: StatsCollector | None = None
_PERSISTENT_EVENT_LOG = None  # EventLog singleton; survives session GC
_PERSISTENT_EVENT_LOG_LOCK = threading.Lock()  # guards one-time init of _PERSISTENT_EVENT_LOG
_PERSISTENT_RECOVERY_COUNT: int = 0  # orphan events recovered at startup
_PERSISTENT_ACTIVE_THREAD = None  # populated when a worker starts; cleared on finished/next=-1
_PERSISTENT_LOG_BUFFER: deque = deque(maxlen=300)  # replayed into new main_view on reattach

# Mirror of MainView's transient UI state so the post-GC main_view can show
# the worker's actual progress / current step instead of resetting to 0.
# Updated in the event handlers; restored before main_view.build() runs.
_PERSISTENT_UI_STATE: dict = {
    "progress_value": 0,
    "progress_total": 0,
    "progress_started_at": None,
    "step_states": ["idle", "idle", "idle", "idle"],
    "phase": "",
    "countdown": 0,
    # Transient UI bits that were not restored before — the old reattach
    # path left the new MainView with default "暫停" button text and no
    # loading dialog even when a worker was paused or stopping. A user
    # caught in either of those states during a rapid-nav session GC saw
    # the UI "reset" to a wrong state.
    "is_paused": False,
    "loading_open": False,
    "loading_message": "",
}


def _activate_view(views: list, idx: int) -> int:
    """Show one view while keeping every view mounted in the Flet tree."""
    try:
        active_idx = int(idx)
    except (TypeError, ValueError):
        active_idx = 0
    if active_idx < 0 or active_idx >= len(views):
        active_idx = 0
    for i, view in enumerate(views):
        view.visible = i == active_idx
    return active_idx


def _handle_page_progress_event(main_view: MainView, data) -> None:
    """Normalize page-progress event payloads before touching MainView."""
    if not data:
        main_view.clear_page_progress()
        return
    if isinstance(data, dict):
        delta = data.get("delta", 0)
        total = data.get("total", 0)
        pid = data.get("pid", "")
    elif isinstance(data, tuple):
        if len(data) >= 3:
            delta, total, pid = data[:3]
        elif len(data) == 2:
            delta, total = data
            pid = ""
        else:
            main_view.clear_page_progress()
            return
    else:
        return
    main_view.update_page_progress(delta, total, pid)

# ── crash-safe flush ───────────────────────────────────────────────────────
# Without this, an unhandled exception (in main thread, in any worker, or
# during interpreter shutdown via a non-window-close path) tears the
# process down without giving the active worker a chance to flush its
# in-memory state (exist_pid set, url_meta dict, author_progress, the
# Step 2 collected PIDs, etc.) back to disk. flush_for_shutdown() is
# already implemented on every worker — we just need to make sure it
# actually fires on every exit path, not only on window-close.
_HOOKS_INSTALLED = False
_EMERGENCY_FLUSH_LOCK = threading.Lock()
_EMERGENCY_FLUSH_DONE = False


def _emergency_flush(reason: str = "unknown") -> None:
    """Idempotent best-effort flush. Safe to call from any path/thread.

    MUST NOT raise — that would mask the original crash traceback.
    """
    global _EMERGENCY_FLUSH_DONE
    with _EMERGENCY_FLUSH_LOCK:
        if _EMERGENCY_FLUSH_DONE:
            return
        _EMERGENCY_FLUSH_DONE = True
    t = _PERSISTENT_ACTIVE_THREAD
    # 沒有 active worker 也沒有可 checkpoint 的 DB 時，整個函式 no-op，
    # 避免在 atexit 路徑（stdout 可能已被 pytest 等收尾關閉）製造 logging
    # I/O 錯誤。實作上：只在有東西要做時才寫 log。
    if t is not None and hasattr(t, "flush_for_shutdown"):
        try:
            _log.warning("_emergency_flush entered: reason=%s", reason)
        except Exception:
            pass
        try:
            t.flush_for_shutdown()
        except Exception:
            try:
                _log.exception("emergency flush_for_shutdown failed")
            except Exception:
                pass
    try:
        from app.core.metadata_db import DB_FILENAME
        _db_path = os.path.join(
            os.getenv("APPDATA", "") or "", "pixiv_download", DB_FILENAME
        )
        if os.path.isfile(_db_path):
            _c = sqlite3.connect(_db_path, timeout=3.0, isolation_level=None)
            try:
                _c.execute("PRAGMA wal_checkpoint(PASSIVE)")
            finally:
                _c.close()
    except Exception:
        try:
            _log.exception("emergency wal_checkpoint failed")
        except Exception:
            pass
    try:
        el = _PERSISTENT_EVENT_LOG
        if el is not None:
            el.close()
    except Exception:
        pass


def _install_crash_hooks() -> None:
    """Wire sys.excepthook / threading.excepthook / atexit into emergency flush.

    Called from main(page). Module-level guard ensures we don't stack hooks
    when Flet re-invokes main(page) after session GC.
    """
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    _HOOKS_INSTALLED = True

    prev_sys_excepthook = sys.excepthook

    def _sys_excepthook(exc_type, exc, tb):
        _emergency_flush(reason=f"sys.excepthook:{exc_type.__name__}")
        try:
            prev_sys_excepthook(exc_type, exc, tb)
        except Exception:
            pass

    sys.excepthook = _sys_excepthook

    prev_threading_excepthook = getattr(threading, "excepthook", None)

    def _threading_excepthook(args):
        exc_type = getattr(args, "exc_type", None)
        name = exc_type.__name__ if exc_type else "Exception"
        _emergency_flush(reason=f"threading.excepthook:{name}")
        try:
            if prev_threading_excepthook is not None:
                prev_threading_excepthook(args)
        except Exception:
            pass

    if hasattr(threading, "excepthook"):
        threading.excepthook = _threading_excepthook

    atexit.register(_emergency_flush, "atexit")


def main(page: ft.Page) -> None:
    init_logging()
    _install_crash_hooks()
    _log.info(
        "main(page) session start: web=%s platform=%s",
        getattr(page, "web", "?"),
        getattr(page, "platform", "?"),
    )
    page.title = "Pixiv 下載器"
    page.theme_mode = _load_theme_mode()
    page.theme = ft.Theme(color_scheme_seed="#0096FA")
    page.window.width = 1100
    page.window.height = 750
    page.padding = 0

    # Reuse the persistent queue + stats so a worker that started in the
    # previous session keeps emitting into the same Queue object after
    # session GC. The first main() call lazily-inits the stats collector.
    global _PERSISTENT_STATS, _PERSISTENT_ACTIVE_THREAD, _PERSISTENT_EVENT_LOG, _PERSISTENT_RECOVERY_COUNT
    event_q: queue.Queue = _PERSISTENT_EVENT_Q
    if _PERSISTENT_STATS is None:
        _PERSISTENT_STATS = StatsCollector()
    stats_collector = _PERSISTENT_STATS

    # ── EventLog: one instance per process lifetime ───────────────────────
    # Constructed only on the first main() call; subsequent calls (Flet
    # session GC reattach) reuse the existing instance so we don't open a
    # second writer to the same JSONL file.
    # The lock prevents two concurrent main() calls (can happen during rapid
    # session GC) from each seeing None and creating separate EventLog instances.
    with _PERSISTENT_EVENT_LOG_LOCK:
        if _PERSISTENT_EVENT_LOG is None:
            _base_path = os.path.join(os.getenv("APPDATA", ""), "pixiv_download")
            os.makedirs(_base_path, exist_ok=True)
            try:
                from app.core.event_log import EventLog, recover_tail
                from app.core.metadata_db import MetadataDB as _MetadataDB
                _el = EventLog(_base_path, **_event_log_kwargs())
                atexit.register(_el.close)
                _PERSISTENT_EVENT_LOG = _el
                # Auto recover_tail if the previous session was unclean.
                # Recovery must run BEFORE any worker thread starts so it
                # doesn't race with live DB writes.
                if _el.last_session_was_unclean:
                    try:
                        _rdb = _MetadataDB(_base_path)
                        try:
                            _n = recover_tail(_rdb, _el.log_dir)
                        finally:
                            _rdb.close()
                        if _n > 0:
                            _log.info(
                                "event_log: recovered %d orphan events from previous session", _n
                            )
                            _PERSISTENT_RECOVERY_COUNT = _n
                    except Exception:
                        _log.exception("event_log: recover_tail failed")
                # STR2: after recovery, durably checkpoint the live DB then drop
                # a cheap 'checkpoint' anchor. This bounds the NEXT crash
                # recovery to just this session's tail even when the user never
                # presses Run (the only other anchor source) and force-kills.
                # Emitted AFTER recover_tail so it never swallows this session's
                # orphans, and after the WAL checkpoint so the anchor only
                # vouches for DB state that is durably on disk.
                try:
                    from app.core.metadata_db import DB_FILENAME as _DBF
                    _ckpt_path = os.path.join(_base_path, _DBF)
                    if os.path.isfile(_ckpt_path):
                        _cc = sqlite3.connect(_ckpt_path, timeout=5.0, isolation_level=None)
                        try:
                            _cc.execute("PRAGMA wal_checkpoint(PASSIVE)")
                        finally:
                            _cc.close()
                    _el.emit("checkpoint", pid=os.getpid())
                except Exception:
                    _log.exception("event_log: startup checkpoint anchor failed")
            except Exception:
                _log.exception("event_log: failed to initialise EventLog")

    event_log = _PERSISTENT_EVENT_LOG

    # Surface any crash-recovery count to the UI now that event_q exists.
    _rc = _PERSISTENT_RECOVERY_COUNT
    if _rc > 0:
        _PERSISTENT_RECOVERY_COUNT = 0
        try:
            event_q.put(WorkerEvent(
                "output",
                f"<p><font color='gray'>偵測到上次未正常結束，已自動補齊 {_rc} 筆事件</font></p>",
            ))
        except Exception:
            pass

    main_view = MainView(page, event_q)
    settings_view = SettingsView(page)
    cookies_view = CookiesView(page, event_q)
    stats_view = StatsView(page, stats_collector)

    run_controller = RunController(main_view, event_q, stats_collector, event_log=event_log)
    main_view._run_controller = run_controller

    # In-app scheduler: fire Run All on the configured schedule. The service
    # reads settings live and skips when a run is already active.
    try:
        from app.core.scheduler_service import SchedulerService
        _sched_cfg = _settings_store().get_section("schedule")
        if bool(_sched_cfg.get("enabled", False)):
            scheduler_service = SchedulerService(
                get_cfg=lambda: _settings_store().get_section("schedule"),
                run_all=lambda: run_controller.run_all(),
                is_active=lambda: getattr(main_view, "_active_thread", None) is not None
                                  and main_view._active_thread.is_alive(),
                emit=lambda html: event_q.put(WorkerEvent("output", html)),
            )
            scheduler_service.start()
            atexit.register(scheduler_service.stop)
            _log.info("scheduler service started")
    except Exception:
        _log.exception("failed to start scheduler service")

    # If a worker survived the previous session GC, hand it to the new
    # main_view so pause/stop still work and the UI shows "running".
    # Replay the buffered log lines + restore progress/step/phase state
    # so the user sees the worker's actual progress instead of a reset
    # 0/0 panel.
    if _PERSISTENT_ACTIVE_THREAD is not None and _PERSISTENT_ACTIVE_THREAD.is_alive():
        main_view._active_thread = _PERSISTENT_ACTIVE_THREAD
        # Restore progress + step indicators BEFORE set_running so the UI
        # reflects "worker still at PID N/M" not "fresh start at 0/M".
        # Direct attribute assignment is safe here: build() hasn't run
        # yet, so update() isn't called on detached controls.
        try:
            main_view._progress_value = int(_PERSISTENT_UI_STATE.get("progress_value", 0))
            main_view._progress_total = int(_PERSISTENT_UI_STATE.get("progress_total", 0))
            main_view._progress_started_at = _PERSISTENT_UI_STATE.get("progress_started_at")
            for i, st in enumerate(_PERSISTENT_UI_STATE.get("step_states", [])[:4]):
                main_view.set_step_state(i, st)
            phase = str(_PERSISTENT_UI_STATE.get("phase", "") or "")
            if phase:
                main_view.set_phase(phase)
            # Restore the countdown text + pause button label. Direct
            # attribute assignment only — set_running() will not be called
            # again for the existing worker, so we must paint the toggled
            # state ourselves. Both controls' update() raises before build()
            # runs and is swallowed by the surrounding try/except.
            cd = int(_PERSISTENT_UI_STATE.get("countdown", 0) or 0)
            if cd > 0:
                main_view._countdown_text.value = f"倒數：{cd} 秒"
            if bool(_PERSISTENT_UI_STATE.get("is_paused", False)):
                main_view._is_paused = True
                main_view._btn_pause.content = "▶ 繼續"
        except Exception:
            _log.exception("failed to restore persisted UI state")
        main_view.set_running(True)
        for html in list(_PERSISTENT_LOG_BUFFER):
            try:
                main_view.append_log(html)
            except Exception:
                pass
        _log.warning(
            "reattached active worker thread to new session: %s (progress=%s/%s)",
            _PERSISTENT_ACTIVE_THREAD,
            main_view._progress_value, main_view._progress_total,
        )
    elif _PERSISTENT_ACTIVE_THREAD is not None:
        _PERSISTENT_ACTIVE_THREAD = None

    # Capture the active worker into the module global whenever
    # RunController spins one up, so a session GC mid-download still
    # leaves us with a handle to pause/stop it after the new main()
    # reattaches.
    _orig_start_step = run_controller._start_step
    def _start_step_with_tracking(n: int) -> None:
        _orig_start_step(n)
        global _PERSISTENT_ACTIVE_THREAD
        _PERSISTENT_ACTIVE_THREAD = main_view._active_thread
        # Snapshot the step-state list right after the new step's "running"
        # flag has been set, so a session GC during this step preserves
        # which step is currently active.
        _PERSISTENT_UI_STATE["step_states"] = list(main_view._step_states)
    run_controller._start_step = _start_step_with_tracking

    views_built = [main_view.build(), settings_view.build(), cookies_view.build(), stats_view.build()]
    _activate_view(views_built, 0)

    content_area = ft.Column(
        controls=views_built,
        expand=True,
    )

    # Debounce nav clicks <150ms apart. All views stay mounted and nav only
    # toggles visibility; otherwise worker progress/stat updates can target
    # controls that were removed from the client tree and destabilize the
    # Flet desktop session during downloads.
    _nav_state = {"idx": -1, "ts": 0.0}
    _nav_lock = threading.Lock()

    async def _reload_views_on_loop(idx: int) -> None:
        try:
            if idx == 1:
                settings_view.reload_cookie_count()
            elif idx == 2:
                cookies_view.reload_from_settings()
        except Exception:
            _log.exception("nav async reload failed for idx=%s", idx)

    def on_nav_change(e: ft.ControlEvent) -> None:
        # Exceptions here must not bubble into Flet's event loop — Flet may
        # respond to an unhandled error by resetting the session, which
        # silently kills any running download. Log and swallow instead.
        try:
            idx = e.control.selected_index
            now = time.monotonic()
            with _nav_lock:
                if idx == _nav_state["idx"] and (now - _nav_state["ts"]) < 0.15:
                    _log.info("nav change → idx=%s (debounced)", idx)
                    return
                _nav_state["idx"] = idx
                _nav_state["ts"] = now
            _log.info("nav change → idx=%s", idx)
            idx = _activate_view(views_built, idx)
            page.update()
            # These helpers update Flet controls, so they must run on the
            # event-loop page thread. Running them on a daemon thread can
            # enqueue patches from the wrong thread and trigger session GC.
            if idx in (1, 2):
                page.run_task(_reload_views_on_loop, idx)
        except Exception:
            _log.exception("on_nav_change failed")

    # Stats auto-refresh runs for the whole session. The stats page remains
    # mounted even while hidden, so its updates no longer race against nav
    # removing/re-adding the statistics controls.
    stats_view.start_auto_refresh()

    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        on_change=on_nav_change,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.HOME_OUTLINED,
                selected_icon=ft.Icons.HOME,
                label="主頁",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SETTINGS_OUTLINED,
                selected_icon=ft.Icons.SETTINGS,
                label="設定",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.COOKIE_OUTLINED,
                selected_icon=ft.Icons.COOKIE,
                label="Cookie",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.BAR_CHART_OUTLINED,
                selected_icon=ft.Icons.BAR_CHART,
                label="統計",
            ),
        ],
    )

    def handle_output(data: str) -> None:
        # Buffer log lines so a session-GC'd successor main() can replay
        # them into its fresh main_view (otherwise the user sees an empty
        # log panel after Flet drops the session, even though the worker
        # is still running fine in the background).
        try:
            _PERSISTENT_LOG_BUFFER.append(str(data))
        except Exception:
            pass
        main_view.append_log(data)

    def handle_progress(data: tuple) -> None:
        current, total = data
        main_view.update_progress(current, total)
        # Mirror to module global so a post-GC successor main() can
        # restore the bar instead of resetting to 0.
        _PERSISTENT_UI_STATE["progress_value"] = main_view._progress_value
        _PERSISTENT_UI_STATE["progress_total"] = main_view._progress_total
        _PERSISTENT_UI_STATE["progress_started_at"] = main_view._progress_started_at

    def handle_page_progress(data) -> None:
        _handle_page_progress_event(main_view, data)

    def handle_countdown(data: int) -> None:
        main_view.update_countdown(data)
        try:
            _PERSISTENT_UI_STATE["countdown"] = int(data)
        except (TypeError, ValueError):
            pass

    def handle_finished(data: str) -> None:
        global _PERSISTENT_ACTIVE_THREAD
        main_view.append_log(f"<p><font color='green'>{data}</font></p>")
        for i, st in enumerate(main_view._step_states):
            if st == "running":
                main_view.set_step_state(i, "done")
        main_view.set_running(False)
        _PERSISTENT_ACTIVE_THREAD = None
        # Run finished — reset persisted state so a future new main()
        # won't show stale "running" UI from this completed run.
        _PERSISTENT_UI_STATE["step_states"] = list(main_view._step_states)
        _PERSISTENT_UI_STATE["progress_value"] = 0
        _PERSISTENT_UI_STATE["progress_total"] = 0
        _PERSISTENT_UI_STATE["progress_started_at"] = None
        _PERSISTENT_UI_STATE["phase"] = ""
        _PERSISTENT_UI_STATE["countdown"] = 0
        _PERSISTENT_UI_STATE["is_paused"] = False
        _PERSISTENT_UI_STATE["loading_open"] = False
        _PERSISTENT_UI_STATE["loading_message"] = ""
        main_view.clear_page_progress()

    def handle_next(data: int) -> None:
        global _PERSISTENT_ACTIVE_THREAD
        if data == -1:
            for i, st in enumerate(main_view._step_states):
                if st == "running":
                    main_view.set_step_state(i, "error")
            _PERSISTENT_ACTIVE_THREAD = None
            main_view.set_running(False)
            _PERSISTENT_UI_STATE["step_states"] = list(main_view._step_states)
            _PERSISTENT_UI_STATE["progress_value"] = 0
            _PERSISTENT_UI_STATE["progress_total"] = 0
            _PERSISTENT_UI_STATE["progress_started_at"] = None
            _PERSISTENT_UI_STATE["is_paused"] = False
            _PERSISTENT_UI_STATE["loading_open"] = False
            _PERSISTENT_UI_STATE["loading_message"] = ""
            _PERSISTENT_UI_STATE["countdown"] = 0
            _PERSISTENT_UI_STATE["phase"] = ""
            main_view.clear_page_progress()
            return
        for i, st in enumerate(main_view._step_states):
            if st == "running":
                main_view.set_step_state(i, "done")
        _PERSISTENT_UI_STATE["step_states"] = list(main_view._step_states)
        run_controller.on_next(data)

    def handle_loading(data) -> None:
        if isinstance(data, tuple) and len(data) == 2:
            busy, message = data
            msg_str = str(message) if message else "正在啟動..."
            main_view.set_loading(bool(busy), msg_str)
            _PERSISTENT_UI_STATE["loading_open"] = bool(busy)
            _PERSISTENT_UI_STATE["loading_message"] = msg_str if bool(busy) else ""
        else:
            main_view.set_loading(bool(data))
            _PERSISTENT_UI_STATE["loading_open"] = bool(data)
            if not bool(data):
                _PERSISTENT_UI_STATE["loading_message"] = ""

    def handle_pause_state(data) -> None:
        # Mirror MainView._is_paused after the user toggled the pause button.
        # No UI action here — the button text was already updated inside
        # MainView; this handler exists only to keep persistence current
        # for a possible post-GC reattach.
        _PERSISTENT_UI_STATE["is_paused"] = bool(data)

    def handle_cookie_status(data) -> None:
        if isinstance(data, tuple):
            if len(data) == 3:
                cookie, status, tested_at = data
                cookies_view.apply_cookie_test_result(
                    str(cookie), str(status), tested_at,
                )
            elif len(data) == 2:
                cookie, status = data
                cookies_view.apply_cookie_test_result(str(cookie), str(status))

    def handle_phase(data) -> None:
        text = str(data) if data else ""
        main_view.set_phase(text)
        _PERSISTENT_UI_STATE["phase"] = text

    disp = EventDispatcher(page, event_q, {
        "output":        handle_output,
        "progress":      handle_progress,
        "page_progress": handle_page_progress,
        "countdown":     handle_countdown,
        "finished":      handle_finished,
        "next":          handle_next,
        "loading":       handle_loading,
        "cookie_status": handle_cookie_status,
        "phase":         handle_phase,
        "pause_state":   handle_pause_state,
    })

    def toggle_theme(e: ft.ControlEvent) -> None:
        page.theme_mode = (
            ft.ThemeMode.DARK
            if page.theme_mode == ft.ThemeMode.LIGHT
            else ft.ThemeMode.LIGHT
        )
        _save_theme_mode(page.theme_mode)
        for view in (main_view, stats_view):
            try:
                view.refresh_theme()
            except Exception:
                pass
        page.update()

    page.appbar = ft.AppBar(
        title=ft.Text("Pixiv 下載器"),
        center_title=False,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        actions=[
            ft.IconButton(
                icon=ft.Icons.LIGHT_MODE,
                tooltip="切換深淺色",
                on_click=toggle_theme,
            ),
        ],
    )

    page.add(
        ft.Row(
            controls=[nav_rail, ft.VerticalDivider(width=1), content_area],
            expand=True,
        )
    )

    # platform_brightness becomes reliable only after page.add(), so re-apply
    # step-card / stats colors once the page is mounted — covers the SYSTEM
    # theme start-on-dark-OS case where __init__ guessed the light palette.
    for view in (main_view, stats_view):
        try:
            view.refresh_theme()
        except Exception:
            _log.exception("initial refresh_theme failed")

    # Restore the modal loading dialog AFTER page.add — set_loading() relies
    # on page.show_dialog() which needs the page's session to be live and
    # attached. If a session GC fired while a worker was being stopped
    # (which holds the "正在停止，等待清理完成..." dialog for up to 60 s)
    # we need to put it back on the new page; otherwise the buttons stay
    # disabled-looking and the user thinks the app is frozen.
    if bool(_PERSISTENT_UI_STATE.get("loading_open", False)):
        msg = str(_PERSISTENT_UI_STATE.get("loading_message", "") or "正在啟動...")
        try:
            main_view.set_loading(True, msg)
        except Exception:
            _log.exception("failed to restore loading dialog")

    # IMPORTANT: dispatcher must run as a task on the asyncio event loop so
    # control.update() and page.update() actually flush to the client. With
    # page.run_thread() patches end up on an asyncio.Queue from the wrong
    # thread and only flush when the user pokes the UI (drag, click).
    page.run_task(disp.run)

    # ── shutdown handling ───────────────────────────────────────────────────
    # Without this, closing the window leaves the dispatcher polling and any
    # in-flight worker thread (incl. its concurrent.futures pool with 30 s
    # request timeouts) blocking interpreter exit via atexit hooks.
    async def _shutdown_and_destroy(reason: str) -> None:
        _log.warning("_shutdown_and_destroy entered: reason=%s", reason)
        try:
            t = getattr(main_view, "_active_thread", None)
            if t is not None:
                if hasattr(t, "flush_for_shutdown"):
                    try:
                        t.flush_for_shutdown()
                    except Exception:
                        _log.exception("flush_for_shutdown failed")
                if hasattr(t, "stop"):
                    try:
                        t.stop()
                    except Exception:
                        _log.exception("active_thread.stop() failed")
            # Merge any un-checkpointed WAL pages into the main DB file so the
            # on-disk state is consistent before os._exit(0) bypasses atexit.
            # PASSIVE mode is non-blocking: checkpoints what it can without
            # waiting for active readers/writers to finish.
            try:
                from app.core.metadata_db import DB_FILENAME
                _db_path = os.path.join(
                    os.getenv("APPDATA", ""), "pixiv_download", DB_FILENAME
                )
                if os.path.isfile(_db_path):
                    _c = sqlite3.connect(_db_path, timeout=3.0, isolation_level=None)
                    _c.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    _c.close()
            except Exception:
                _log.exception("wal_checkpoint failed during shutdown")
        finally:
            try:
                if event_log is not None:
                    event_log.close()
            except Exception:
                pass
            disp.stop()
            # Catch BaseException — asyncio.CancelledError is BaseException
            # in 3.8+ and was previously letting control skip os._exit(0),
            # which left the Python process alive but with the active
            # download thread already stopped: app appeared to "reset to
            # home" with downloads silently killed.
            try:
                await page.window.destroy()
            except BaseException:
                _log.exception("page.window.destroy() failed (continuing to exit)")
            _log.warning("calling os._exit(0)")
            # concurrent.futures registers an atexit hook that joins its
            # daemon workers; an in-flight requests.get with a 30 s timeout
            # would otherwise block the process from exiting.
            os._exit(0)

    async def on_window_event(e) -> None:
        ev_type = getattr(e, "type", None)
        _log.info("window event: %s", ev_type)
        if ev_type == ft.WindowEventType.CLOSE:
            await _shutdown_and_destroy("window_close")

    async def on_disconnect(e) -> None:
        # Flet's docs say on_disconnect only fires on web tab close. In
        # desktop mode it should never fire during normal use — if it does,
        # the previous behaviour (stop active thread + os._exit) wiped any
        # running download, so we now log-and-ignore on desktop and only
        # tear down on actual web disconnects.
        is_web = bool(getattr(page, "web", False))
        _log.warning(
            "on_disconnect fired: web=%s — %s",
            is_web,
            "tearing down" if is_web else "ignoring (desktop)",
        )
        if is_web:
            await _shutdown_and_destroy("web_disconnect")

    page.window.prevent_close = True
    page.window.on_event = on_window_event
    page.on_disconnect = on_disconnect


if __name__ == "__main__":
    ft.app(target=main)
