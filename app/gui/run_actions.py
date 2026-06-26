"""Step orchestration for the Flet GUI.

Reads settings from SettingsStore (no Qt UI access), constructs the four
worker threads with proper args, and exposes a small RunController that
the views layer drives via button callbacks. Chained "Run All" mode is
implemented by reacting to WorkerEvent("next", N) inside on_next().
"""
from __future__ import annotations
import os
import threading
from datetime import datetime
from queue import Queue

from app.core.settings_store import SettingsStore
from app.core.worker_event import WorkerEvent
from app.core.account_scheduler import AccountState, AccountScheduler
from app.core.proxy_utils import parse_proxy_url
from app.core.pixiv_thread_utils import (
    safe_read_json, sync_exist_pid_with_download_folder,
)
from app.core.metadata_db import DB_FILENAME, MetadataDB
from app.core.live_settings import LiveSettings
from app.core import thread_following, thread_pid_scan, thread_url_fetch, thread_download
from app.gui.cookie_validation import _RETEST_INTERVAL_SEC, _CookieValidationMixin
import contextlib

DEFAULT_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)

# Cookie validation + auth persistence (incl. _RETEST_INTERVAL_SEC, re-exported
# above for tests) moved to cookie_validation._CookieValidationMixin (file-size
# refactor); RunController inherits it. _RETEST_INTERVAL_SEC is kept importable
# from here (tests do ``from app.gui.run_actions import _RETEST_INTERVAL_SEC``);
# listing it in __all__ marks it as a deliberate re-export.
__all__ = ["RunController", "DEFAULT_AGENT", "_RETEST_INTERVAL_SEC"]


def _coerce_int(value, default: int) -> int:
    """Tolerant int parse for settings.json values that may be hand-edited.

    Falls back to *default* on TypeError/ValueError. Keeps thread construction
    from crashing when a user pastes a non-numeric value into a settings field.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _data_path() -> str:
    p = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(p, exist_ok=True)
    return p


def _store() -> SettingsStore:
    s = SettingsStore(_data_path())
    s.migrate_from_legacy()
    return s


def _read_event_log_cfg() -> dict:
    """Return the event_log sub-section from settings, or {} on any error."""
    try:
        section = _store().get_section("event_log")
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def _write_event_log_cfg(new_cfg: dict) -> None:
    """Persist new_cfg as the event_log section. Silently ignored on error."""
    with contextlib.suppress(Exception):
        _store().update_fields("event_log", new_cfg)


def _agent(auth: dict) -> str:
    return str(auth.get("agent") or "").strip() or DEFAULT_AGENT


def _load_author_list() -> list[str]:
    path = _data_path()
    j = safe_read_json(os.path.join(path, "following.json"), None)
    if isinstance(j, list) and j:
        return [str(x) for x in j]
    txt_path = os.path.join(path, "following.txt")
    if os.path.isfile(txt_path):
        with open(txt_path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return []


class RunController(_CookieValidationMixin):
    """Wires step buttons to worker threads and chains them in Run-All mode."""

    # Class-level default so tests that do ``RunController.__new__(RunController)``
    # (bypassing __init__) can still read ``self._event_log`` without AttributeError.
    _event_log = None

    force_combined = False  # CLI 'combined' step sets this to force combined mode

    # Baseline settings snapshot captured when the active run's thread was built;
    # notify_settings_saved() diffs against it. Class default so __new__-based
    # tests can read it without AttributeError.
    _active_snapshot = None

    def __init__(self, main_view, event_q: Queue, stats_collector=None, event_log=None):
        self._main_view = main_view
        self._event_q = event_q
        self._run_all_mode = False
        self._stats_collector = stats_collector
        self._event_log = event_log
        # Shared live-settings reader: lets a mid-run 「儲存設定」 take effect on
        # the next item for in-scope settings. Degrades to None (snapshot-only)
        # if the data dir is unavailable.
        try:
            self._live = LiveSettings(_data_path())
        except Exception:
            self._live = None
        self._active_snapshot = None
        # Serialises _start_step's idle-check + _active_thread assignment so a
        # scheduler tick and a user click (or two scheduler ticks) can't both
        # launch a run that then writes the same on-disk state concurrently.
        self._start_lock = threading.Lock()

    def _is_run_active(self) -> bool:
        t = getattr(self._main_view, "_active_thread", None)
        return t is not None and t.is_alive()

    def _backup_db(self) -> None:
        """Run at most once per local-time day. Persists last-success date via
        event_log.last_snapshot_date in settings. Skipped entirely when
        event_log.auto_snapshot_on_run is False."""
        try:
            import datetime
            cfg = _read_event_log_cfg()

            if not cfg.get("auto_snapshot_on_run", True):
                return

            today = datetime.datetime.now().strftime("%Y-%m-%d")
            if cfg.get("last_snapshot_date") == today:
                return

            db_path = os.path.join(_data_path(), DB_FILENAME)
            if not os.path.isfile(db_path):
                return

            retention_days = int(cfg.get("retention_days", 60))
            max_history = max(3, retention_days // 14)

            from app.core.metadata_db import MetadataDB
            ok = MetadataDB(_data_path(), event_log=self._event_log).backup_db(
                max_history=max_history
            )
            if not ok:
                return

            _write_event_log_cfg({**cfg, "last_snapshot_date": today})

            # STR3: the snapshot is a verified, fsync'd full DB image, so event
            # files fully older than it are redundant. Prune them (keeping a
            # 2-day margin for tools/replay_events.py), bounding the log to a
            # small tail by construction. compact_before_date never crosses the
            # latest anchor or the current file.
            try:
                if self._event_log is not None:
                    import datetime as _dt
                    cutoff = (
                        _dt.datetime.now() - _dt.timedelta(days=2)
                    ).strftime("%Y%m%d")
                    self._event_log.compact_before_date(cutoff)
            except Exception:
                pass
        except Exception:
            # Best-effort; never break Run All if snapshot fails.
            pass

    def run_step(self, n: int) -> None:
        self._run_all_mode = False
        self._backup_db()
        self._start_step(n, require_idle=True)

    def run_all(self) -> None:
        self._run_all_mode = True
        self._backup_db()
        try:
            source_mode = _store().get_section("download").get("source_mode", "following")
        except Exception:
            source_mode = "following"
        self._start_step(2 if source_mode == "bookmarks" else 1, require_idle=True)

    def on_next(self, n: int) -> None:
        if n == -1 or not self._run_all_mode:
            return
        if 1 <= n <= 4:
            # Chaining: the previous step's worker may still be alive (about to
            # return) when this fires, so do NOT require idle here — that is a
            # legitimate hand-off, not a duplicate run.
            self._start_step(n)

    def _log(self, html: str) -> None:
        self._event_q.put(WorkerEvent("output", html))

    # ── Live-apply on save ───────────────────────────────────────────────────
    # Which settings.json keys take effect mid-run (green notice, applied on the
    # next item by the worker's _apply_live_settings_if_changed) vs which need a
    # fresh run because they pick the pipeline shape at construction (grey).
    _LIVE_KEY_LABELS = {
        "download.like_num": "最低讚數",
        "download.ban_tag": "排除標籤",
        "download.must_tag": "必含標籤",
        "download.special_like_rules": "標籤讚數規則",
        "download.download_time": "下載日期門檻",
        "download.rescrape_within_days": "重抓天數",
        "download.path": "下載路徑",
        "download.filename_template": "檔名範本",
        "download.tag_strip_brackets": "Tag 過濾括號",
        "download.tag_strip_special_chars": "Tag 過濾特殊符號",
        "filter.nogif": "過濾 GIF",
        "filter.notag": "無 tag 不下載",
        "filter.notime": "無時間不下載",
        "directory.create_dir": "依作者建資料夾",
        "directory.no_R18G_dir": "R-18G 分資料夾",
        "directory.no_R18_dir": "R-18 分資料夾",
        "directory.ai_gen_dir": "AI 生成分資料夾",
        "performance.pid_cooldown_avg": "帳號冷卻時間",
        "performance.pid_wait_nocookie_min": "無 Cookie 等待下限",
        "performance.pid_wait_nocookie_max": "無 Cookie 等待上限",
        "performance.intra_pid_wait_min": "頁間等待下限",
        "performance.intra_pid_wait_max": "頁間等待上限",
        "jxl.enable": "JXL 轉換",
        "jxl.effort": "JXL 壓縮等級",
        "jxl.delete_original": "JXL 刪除原檔",
        "jxl.cjxl_path": "cjxl 路徑",
    }
    _NEXT_RUN_KEY_LABELS = {
        "download.combined_mode": "邊查邊下",
        "download.source_mode": "作品來源",
        "download.following_scope": "追隨範圍",
        "download.bookmark_scope": "收藏範圍",
        "performance.single_thread_mode": "單執行緒",
        "download.author_order": "依作者順序下載",
        "download.force_full_rescan": "強制完整重掃",
    }

    @staticmethod
    def _diff_setting_labels(baseline: dict, current: dict, labels: dict) -> list[str]:
        """Labels for the keys whose value differs between baseline and current."""
        changed = []
        for key, label in labels.items():
            section, _, field = key.partition(".")
            before = (baseline.get(section) or {}).get(field)
            after = (current.get(section) or {}).get(field)
            if before != after:
                changed.append(label)
        return changed

    def notify_settings_saved(self) -> None:
        """Emit a notice after a mid-run save: what applied live (next item) vs
        what needs the next run. No-op when no task is currently running."""
        active = getattr(self._main_view, "_active_thread", None)
        if active is None or not active.is_alive():
            return
        live = getattr(self, "_live", None)
        if live is None:
            return
        current = live.sections()
        baseline = getattr(self, "_active_snapshot", None) or {}
        live_changed = self._diff_setting_labels(baseline, current, self._LIVE_KEY_LABELS)
        next_changed = self._diff_setting_labels(baseline, current, self._NEXT_RUN_KEY_LABELS)
        if live_changed:
            self._log(
                "<p><font color='green'>已即時套用（下一個項目起生效）："
                + "、".join(live_changed) + "</font></p>"
            )
        if next_changed:
            self._log(
                "<p><font color='gray'>"
                + "、".join(next_changed) + " 將於下次執行套用</font></p>"
            )
        self._active_snapshot = current

    # Cookie test/validation + auth persistence (_extract_cookie_entries /
    # _cookie_cache_is_fresh / _partition_cookies_by_cache / _run_one_cookie_test
    # / _test_cookies / _persist_cookie_statuses / _refresh_cookie_timestamp /
    # _invalidate_cookie_status / _attach_aliases / _validate_cookies_for_step)
    # moved to cookie_validation._CookieValidationMixin (file-size refactor);
    # inherited.

    def _build_scheduler(
        self,
        auth: dict,
        valid_cookies: list[str],
        pause_event,
        stop_event,
    ) -> AccountScheduler:
        """Build an AccountScheduler from a pre-validated cookie list.

        Alias is sourced from auth.cookies_aliases, proxy from
        auth.cookie_proxy_map. The scheduler reads pid_cooldown_avg from
        settings on every release(), so live slider edits in the settings
        UI take effect without restart.
        """
        alias_map = auth.get("cookies_aliases") or {}
        proxy_map = auth.get("cookie_proxy_map") or {}
        accounts: list[AccountState] = []
        for i, cookie in enumerate(valid_cookies):
            alias = alias_map.get(cookie) or f"Cookie {i + 1}"
            raw_proxy = proxy_map.get(cookie) or None
            proxy_url = parse_proxy_url(raw_proxy) if raw_proxy else None
            accounts.append(AccountState(cookie=cookie, alias=alias, proxy_url=proxy_url))

        return AccountScheduler(
            accounts=accounts,
            get_cooldown_avg=lambda: float(
                _store().get_section("performance").get("pid_cooldown_avg", 35)
            ),
            pause_event=pause_event,
            stop_event=stop_event,
            emit=self._log,
            q=self._event_q,
            on_disable=lambda acc: self._invalidate_cookie_status(acc.cookie),
            on_success=lambda acc: self._refresh_cookie_timestamp(acc.cookie),
        )

    def _start_step(self, n: int, *, require_idle: bool = False) -> None:
        # The idle-check and the _active_thread assignment must be one atomic
        # critical section, else a scheduler tick and a user click both pass the
        # check and both start a run. require_idle is True only for the entry
        # points (run_step / run_all); chaining (on_next) passes False because
        # the previous step's worker is legitimately still finishing.
        with self._start_lock:
            if require_idle and self._is_run_active():
                self._log("<p><font color='gray'>已有任務執行中，忽略本次啟動</font></p>")
                return
            try:
                t = self._build_thread(n)
            except Exception as err:
                self._log(f"<p><font color='red'>步驟 {n} 建立執行緒失敗：{err}</font></p>")
                self._main_view.set_running(False)
                self._main_view.set_step_state(n - 1, "error")
                # No worker thread started -> emit a terminal event so a headless
                # _pump unblocks immediately (exit 1) instead of waiting out its
                # 600s queue-starvation timeout. on_next(-1) is a no-op in the GUI.
                self._event_q.put(WorkerEvent("next", -1))
                return
            if t is None:
                self._main_view.set_running(False)
                # Same terminal event for the "build returned None" path (invalid
                # cookies / missing User ID / empty following list).
                self._event_q.put(WorkerEvent("next", -1))
                return
            self._main_view._active_thread = t
            self._main_view.set_running(True)
            # Drive the "本次執行" elapsed timer from the run lifecycle, not from
            # inside Step 4: a fresh run (require_idle) resets it; a chained
            # Run-All step resumes the same timer so elapsed spans the whole run
            # (combined mode + Step 1/2/3-only runs are covered too — they never
            # reach download_thread.run() where the reset used to live).
            if self._stats_collector is not None:
                if require_idle:
                    self._stats_collector.reset_session()
                else:
                    self._stats_collector.resume_session()
            for i in range(4):
                self._main_view.set_step_state(i, "idle")
            self._main_view.set_step_state(n - 1, "running")
            self._log(f"<p><font color='gray'>--- 步驟 {n} 開始 ---</font></p>")
            t.start()

    def _build_step1(self, auth, agent, dl, flt):
        if str(dl.get("source_mode", "following") or "following") == "bookmarks":
            self._log("<p><font color='gray'>收藏模式略過步驟 1，直接抓收藏 PID</font></p>")
            return None
        userid = str(auth.get("userid", "")).strip()
        if not userid:
            self._log("<p><font color='red'>請先在「設定」填入 User ID</font></p>")
            return None
        valid_cookies = self._validate_cookies_for_step(auth, agent, 1)
        if not valid_cookies:
            return None
        return thread_following.get_following(
            self._event_q,
            userid,
            valid_cookies[0],
            agent,
            str(dl.get("following_scope", "all") or "all"),
        )

    def _build_step2(self, auth, agent, dl, perf, path):
        source_mode = str(dl.get("source_mode", "following") or "following")
        authors = [] if source_mode == "bookmarks" else _load_author_list()
        if source_mode != "bookmarks" and not authors:
            self._log(
                "<p><font color='red'>找不到 following 清單，請先執行步驟 1</font></p>"
            )
            return None
        bookmark_user_id = str(auth.get("userid", "") or "").strip()
        if source_mode == "bookmarks" and not bookmark_user_id:
            self._log("<p><font color='red'>請先在「設定」填入 User ID</font></p>")
            return None
        valid_cookies = self._validate_cookies_for_step(auth, agent, 2)
        if not valid_cookies:
            return None
        force_rescan = (
            bool(dl.get("force_full_rescan", False))
            or bool(getattr(self, "force_rescan", False))
        )
        t = thread_pid_scan.get_pixiv_author_imgID_Thread(
            self._event_q,
            authors,
            agent,
            path,
            self._attach_aliases(valid_cookies, auth),
            MetadataDB(path).closed_artwork_set(),
            bool(perf.get("single_thread_mode", False)),
            stats_collector=self._stats_collector,
            event_log=self._event_log,
            author_order=bool(dl.get("author_order", False)),
            force_rescan=force_rescan,
            source_mode=source_mode,
            bookmark_scope=str(dl.get("bookmark_scope", "all") or "all"),
            bookmark_user_id=bookmark_user_id,
        )
        # One-shot: consume the GUI flag so later Step 2 runs stop ignoring the
        # 30-day skip. (The CLI --force-rescan path sets self.force_rescan, which
        # is per-invocation and needs no reset.)
        if dl.get("force_full_rescan", False):
            with contextlib.suppress(Exception):
                _store().update_fields("download", {"force_full_rescan": False})
        t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
        return t

    def _legacy_scan_paths(self) -> list[str]:
        """Optional list of legacy download folders to also scan for already-
        downloaded PIDs. Configured via settings.json
        ``download.legacy_scan_paths: [str]`` — useful when the user changes
        download path; without this, previously-downloaded PIDs in the old
        folder aren't tracked and Step 3/4 re-fetch them.
        """
        try:
            dl_section = _store().get_section("download")
            raw = dl_section.get("legacy_scan_paths") or []
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        out = []
        for p in raw:
            s = str(p or "").strip()
            if s and os.path.isdir(s):
                out.append(s)
        return out

    def _sync_exist_pid_from_download_folder(self, dl_path: str, base_path: str, step_num: int) -> None:
        """Recursively scan the configured download folder (plus any
        ``download.legacy_scan_paths`` extras) and union newly-detected PIDs
        into exist_pid.json. Legacy paths are scanned with the same cached
        file-count optimisation, so re-runs are cheap once the cache warms up.
        Replaces the legacy _sync_exist_pid_before_step34 helper that was
        lost during the PyQt5→Flet migration.
        """
        primary = (dl_path or "").strip()
        extras = self._legacy_scan_paths()
        targets = ([primary] if primary else []) + extras
        if not targets:
            return
        for target in targets:
            label = "（主路徑）" if target == primary else "（舊路徑）"
            try:
                result = sync_exist_pid_with_download_folder(
                    base_path,
                    target,
                    recursive=True,
                    event_log=self._event_log,
                )
                tag = "[快取]" if result.get("used_cache") else "[掃描]"
                self._log(
                    f"<p><font color='gray'>[exist_pid] {tag} 步驟 {step_num} 前掃描{label}："
                    f"檔案={result.get('scanned_files', 0)}, "
                    f"PID={result.get('scanned_pid_count', 0)}, "
                    f"新增={result.get('added_from_download', 0)}</font></p>"
                )
            except Exception as err:
                self._log(
                    f"<p><font color='red'>[exist_pid] 掃描{label}失敗（{target}）：{err}</font></p>"
                )

    def _build_step3(self, auth, agent, dl, perf, path):
        authors = _load_author_list()
        valid_cookies = self._validate_cookies_for_step(auth, agent, 3)
        if not valid_cookies:
            return None
        self._sync_exist_pid_from_download_folder(
            str(dl.get("path", "")), path, step_num=3,
        )
        t = thread_url_fetch.get_img_url_thread(
            q=self._event_q,
            Author_list=authors,
            Agent=agent,
            cookies=self._attach_aliases(valid_cookies, auth),
            exist_pid=MetadataDB(path).closed_artwork_set(),
            ban_tag=list(dl.get("ban_tag", [])),
            must_tag=list(dl.get("must_tag", [])),
            like_num=int(dl.get("like_num", 0)),
            r18_like_num=int(dl.get("r18_like_num", 0)),
            no_to_check=[],
            base_path=path,
            single_thread_mode=bool(perf.get("single_thread_mode", False)),
            pid_wait_nocookie_min=int(perf.get("pid_wait_nocookie_min", 1)),
            pid_wait_nocookie_max=int(perf.get("pid_wait_nocookie_max", 6)),
            special_like_rules=[],
            stats_collector=self._stats_collector,
            event_log=self._event_log,
            rescrape_within_days=_coerce_int(dl.get("rescrape_within_days", 365), 365),
            live=self._live,
        )
        t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
        return t

    def _build_combined(self, auth, agent, dl, flt, perf, directory, jxl, path):
        authors = _load_author_list()
        valid_cookies = self._validate_cookies_for_step(auth, agent, 3)
        if not valid_cookies:
            return None
        dl_path = str(dl.get("path", "")).strip()
        if not dl_path:
            self._log("<p><font color='red'>請先在「設定」指定下載路徑</font></p>")
            return None
        self._sync_exist_pid_from_download_folder(dl_path, path, step_num=3)
        from app.core import thread_combined
        t = thread_combined.combined_thread(
            q=self._event_q, Author_list=authors, Agent=agent,
            cookies=self._attach_aliases(valid_cookies, auth),
            exist_pid=MetadataDB(path).closed_artwork_set(),
            ban_tag=list(dl.get("ban_tag", [])),
            must_tag=list(dl.get("must_tag", [])),
            like_num=int(dl.get("like_num", 0)),
            r18_like_num=int(dl.get("r18_like_num", 0)), no_to_check=[], base_path=path,
            single_thread_mode=bool(perf.get("single_thread_mode", False)),
            download_path=dl_path,
            download_time=self._parse_download_time(dl.get("download_time")),
            nogif=bool(flt.get("nogif", False)), notag=bool(flt.get("notag", False)),
            notime=bool(flt.get("notime", False)),
            create_dir=bool(directory.get("create_dir", False)),
            no_R18G_dir=bool(directory.get("no_R18G_dir", False)),
            no_R18_dir=bool(directory.get("no_R18_dir", False)),
            intra_pid_wait_min=int(perf.get("intra_pid_wait_min", 5)),
            intra_pid_wait_max=int(perf.get("intra_pid_wait_max", 15)),
            pid_wait_nocookie_min=int(perf.get("pid_wait_nocookie_min", 1)),
            pid_wait_nocookie_max=int(perf.get("pid_wait_nocookie_max", 6)),
            jxl_enable=bool(jxl.get("enable", False)),
            jxl_cjxl_path=str(jxl.get("cjxl_path", "")),
            jxl_delete_original=bool(jxl.get("delete_original", False)),
            jxl_effort=int(jxl.get("effort", 7)),
            jxl_skip_gif=bool(jxl.get("skip_gif", True)),
            ai_gen_dir=bool(directory.get("ai_gen_dir", False)),
            filename_template=str(dl.get("filename_template", "") or ""),
            tag_strip_brackets=bool(dl.get("tag_strip_brackets", False)),
            tag_strip_special_chars=bool(dl.get("tag_strip_special_chars", False)),
            author_order=bool(dl.get("author_order", False)),
            combined_workers=_coerce_int(dl.get("combined_workers", 1), 1),
            set_file_mtime=bool(dl.get("set_file_mtime", True)),
            download_deadline_sec=float(perf.get("download_deadline_sec", 120) or 120),
            rescrape_within_days=_coerce_int(dl.get("rescrape_within_days", 365), 365),
            stats_collector=self._stats_collector, event_log=self._event_log,
            live=self._live,
        )
        t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
        return t

    @staticmethod
    def _parse_download_time(dt_str):
        """Parse download_time setting; falls back to epoch on any error."""
        s = str(dt_str or "").strip()
        if not s:
            return datetime(1970, 1, 1)
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime(1970, 1, 1)

    def _build_step4(self, auth, agent, dl, flt, perf, directory, jxl):
        dl_path = str(dl.get("path", "")).strip()
        if not dl_path:
            self._log("<p><font color='red'>請先在「設定」指定下載路徑</font></p>")
            return None
        dt = self._parse_download_time(dl.get("download_time"))
        valid_cookies = self._validate_cookies_for_step(auth, agent, 4)
        if not valid_cookies:
            return None
        self._sync_exist_pid_from_download_folder(dl_path, _data_path(), step_num=4)
        t = thread_download.download_thread(
            q=self._event_q,
            nogif=bool(flt.get("nogif", False)),
            notag=bool(flt.get("notag", False)),
            notime=bool(flt.get("notime", False)),
            create_dir=bool(directory.get("create_dir", False)),
            download_path=dl_path,
            cookies=self._attach_aliases(valid_cookies, auth),
            agent=agent,
            download_time=dt,
            no_R18G_dir=bool(directory.get("no_R18G_dir", False)),
            no_R18_dir=bool(directory.get("no_R18_dir", False)),
            single_thread_mode=bool(perf.get("single_thread_mode", False)),
            intra_pid_wait_min=int(perf.get("intra_pid_wait_min", 5)),
            intra_pid_wait_max=int(perf.get("intra_pid_wait_max", 15)),
            jxl_enable=bool(jxl.get("enable", False)),
            jxl_cjxl_path=str(jxl.get("cjxl_path", "")),
            jxl_delete_original=bool(jxl.get("delete_original", False)),
            jxl_effort=int(jxl.get("effort", 7)),
            jxl_skip_gif=bool(jxl.get("skip_gif", True)),
            like_num=int(dl.get("like_num", 0)),
            r18_like_num=int(dl.get("r18_like_num", 0)),
            ban_tag=list(dl.get("ban_tag", [])),
            must_tag=list(dl.get("must_tag", [])),
            special_like_rules=[],
            ai_gen_dir=bool(directory.get("ai_gen_dir", False)),
            filename_template=str(dl.get("filename_template", "") or ""),
            tag_strip_brackets=bool(dl.get("tag_strip_brackets", False)),
            tag_strip_special_chars=bool(dl.get("tag_strip_special_chars", False)),
            author_order=bool(dl.get("author_order", False)),
            set_file_mtime=bool(dl.get("set_file_mtime", True)),
            download_deadline_sec=float(perf.get("download_deadline_sec", 120) or 120),
            stats_collector=self._stats_collector,
            event_log=self._event_log,
            live=self._live,
        )
        t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
        return t

    def _build_thread(self, n: int):
        store = _store()
        auth = store.get_section("auth")
        dl = store.get_section("download")
        flt = store.get_section("filter")
        perf = store.get_section("performance")
        directory = store.get_section("directory")
        jxl = store.get_section("jxl")
        agent = _agent(auth)
        path = _data_path()

        # Baseline for the live-apply save notice (what this run was built from).
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._active_snapshot = self._live.sections()

        if n == 1:
            return self._build_step1(auth, agent, dl, flt)
        if n == 2:
            return self._build_step2(auth, agent, dl, perf, path)
        if n == 3:
            if bool(dl.get("combined_mode", False)) or getattr(self, "force_combined", False):
                return self._build_combined(auth, agent, dl, flt, perf, directory, jxl, path)
            return self._build_step3(auth, agent, dl, perf, path)
        if n == 4:
            return self._build_step4(auth, agent, dl, flt, perf, directory, jxl)
        return None
