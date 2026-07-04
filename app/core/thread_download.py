import contextlib
import os
import shutil
import datetime
import re
import threading
import concurrent.futures
import requests
from queue import Queue, Empty
from pixiv_api import *  # noqa: F403  (intentional: re-exports Pixiv_info/gif_download/etc.)
from app.core.worker_event import WorkerEvent
from app import i18n
import pixiv_api
from app.core.pixiv_thread_utils import (
    safe_meta_count as _safe_meta_count,
    append_diagnostic_event,
    atomic_write_text,
    count_text_lines,
    init_cookie_fields,
    normalize_filter_tags,
    normalize_pid,
    output_err,
    to_int_lenient,
    write_all_url_file,
)
from app.core.pixiv_thread_base import (
    DOWNLOAD_DEADLINE_SEC,
    DownloadStopped,
    PauseableThread,
)
from app.core.step4_filename import _FilenameMixin
from app.core.step4_jxl_conversion import _JXLMixin
from app.core.step4_filters import _Step4FiltersMixin
from app.core.step4_media import _Step4MediaMixin
from app.core.step4_legacy_args import _Step4LegacyArgsMixin
from app.core.step4_pacing import _Step4PacingMixin
from app.core.step4_folder_list import _Step4FolderListMixin
from app.core.step4_db_sync import _Step4DbSyncMixin
# Author-ordering primitives moved to step4_author_order (file-size refactor);
# re-imported here so ``thread_download.compute_author_order`` (read by
# thread_combined / thread_pid_scan) and the in-module references keep working.
from app.core.step4_author_order import (  # noqa: F401  (facade re-export)
    _leading_pid_int,
    _within_author_sorted,
    compute_author_order,
)
from app.core import diag_log

# Boundary guard: below this much free space on the download drive the worker
# stops instead of grinding out 0-byte / failed writes (2026-07-04 incident:
# F: hit 0 bytes free and a 4-day combined run mass-failed 35k pages).
LOW_DISK_MIN_FREE_BYTES = 100 * 1024 * 1024


# Tag-cleanup regexes (_ZERO_WIDTH_RE / _BRACKET_CONTENT_RE /
# _DECORATIVE_CHARS_RE) and the filename/tag rendering helpers moved to
# step4_filename._FilenameMixin (file-size refactor). download_thread inherits
# the mixin, so they remain reachable as self._build_download_filename etc.


# Author-ordering primitives (_leading_pid_int / _within_author_sorted /
# compute_author_order) moved to step4_author_order (file-size refactor) and
# re-imported above so the module facade is unchanged.


class download_thread(PauseableThread, _FilenameMixin, _JXLMixin,
                      _Step4FiltersMixin, _Step4MediaMixin, _Step4LegacyArgsMixin,
                      _Step4PacingMixin, _Step4FolderListMixin, _Step4DbSyncMixin):
    pid_max=0
    pid_now=0
    path=os.getenv('APPDATA')+r'/pixiv_download/'
    timelock = threading.Lock()
    def __init__(
        self,
        q,
        nogif,
        notag,
        notime,
        create_dir,
        download_path,
        cookies,
        agent,
        download_time,
        no_R18G_dir,
        no_R18_dir=False,
        single_thread_mode=False,
        intra_pid_wait_min=1,
        intra_pid_wait_max=3,
        jxl_enable=False,
        jxl_cjxl_path=r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe",
        jxl_delete_original=False,
        jxl_effort=7,
        jxl_skip_gif=True,
        scheduler=None,
        stats_collector=None,
        *legacy_args,
        event_log=None,
        defer_step4_scan=False,
        db_base_path=None,
        live=None,
        **legacy_kwargs,
    ):
        super().__init__(q, scheduler=scheduler)
        self._event_log = event_log
        # Live-apply: re-pull in-scope settings at each PID boundary when the
        # user saves mid-run. None -> behaves exactly as before (snapshot only).
        self._live = live
        self._live_sig = None
        self._init_basic_options(
            nogif, notag, notime, create_dir, download_path, cookies, agent,
            download_time, no_R18G_dir, no_R18_dir, single_thread_mode, stats_collector,
        )
        self.intra_pid_wait_min, self.intra_pid_wait_max = self._resolve_intra_pid_wait_range(
            intra_pid_wait_min, intra_pid_wait_max
        )
        self._legacy_pid_cooldown_avg = self._load_legacy_pid_cooldown_avg()

        # Backward compatibility: accept older positional/keyword constructor calls.
        overrides = self._apply_legacy_constructor_args(legacy_args, legacy_kwargs)
        self.ai_gen_dir = overrides.get("ai_gen_dir", self.ai_gen_dir)
        self.author_order = bool(overrides.get("author_order", False))
        self.set_file_mtime = bool(overrides.get("set_file_mtime", True))
        # Total per-page wall-clock download budget (seconds). _stream_to_sink
        # aborts a trickling/wedged transfer past this so the worker can never
        # hang forever (the 2026-06-21 freeze). Set before the defer_step4_scan
        # early-return so combined mode's downloader gets it too.
        self._download_deadline_sec = float(
            overrides.get("download_deadline_sec") or DOWNLOAD_DEADLINE_SEC
        )
        self._init_jxl_config(
            overrides.get("jxl_enable", jxl_enable),
            overrides.get("jxl_cjxl_path", jxl_cjxl_path),
            overrides.get("jxl_delete_original", jxl_delete_original),
            overrides.get("jxl_effort", jxl_effort),
            overrides.get("jxl_skip_gif", jxl_skip_gif),
        )
        self._init_filter_state(
            overrides.get("like_num", 0),
            overrides.get("ban_tag", []),
            overrides.get("must_tag", []),
            overrides.get("special_like_rules", []),
            overrides.get("filename_template", ""),
            tag_strip_brackets=bool(overrides.get("tag_strip_brackets", False)),
            tag_strip_special_chars=bool(overrides.get("tag_strip_special_chars", False)),
            r18_like_num=overrides.get("r18_like_num", 0),
        )
        self._init_step4_paths_and_state()

        os.makedirs(self.download_path, exist_ok=True)
        # The metadata DB may live in a different directory than ``self.path``
        # (combined mode shares the fetcher's data dir while keeping err_url /
        # all_url writes under ``self.path``). ``self.path`` is left untouched.
        self._db_base = db_base_path or self.path
        # Order matters: ``_load_initial_exist_pid_set`` now reads from
        # ``v_closed_artworks`` in the SQLite cache, so ``_metadata_db``
        # has to be initialised first.
        self.url_meta = self._load_initial_url_meta()
        self._metadata_db = self._init_metadata_db(self.url_meta)
        if defer_step4_scan:
            # Lightweight init: skip the Step-4-only heavy scans (closed-set
            # read, mirror, stats, all_url load + task prep). Safe defaults
            # keep every attribute the rest of the class reads present.
            self.exist_pid = set()
            self.allurl = []
            self._task_filter_stats = {}
            self.pid_max = 0
            return
        self.exist_pid = self._load_initial_exist_pid_set()
        # No mirror-back: exist_pid is read straight from the DB's closed set
        # (``_load_initial_exist_pid_set`` → ``closed_artwork_set``), so
        # re-importing it is a guaranteed no-op that would only re-scan ~1.1M
        # rows, emit a giant event-log line, and invalidate the closed-set
        # cache. Externally-dropped files are registered by
        # ``_sync_exist_pid_from_download_folder`` before this thread builds.
        self._emit_metadata_db_stats(stage="Step4")
        self._warn_if_meta_empty_with_like_filter()
        self._read_all_url_file_into_state()

        raw_allurl_count = len(self.allurl)
        self.allurl, self._task_filter_stats = self._prepare_download_tasks(self.allurl)
        self.pid_max = len(self.allurl)
        self._emit_step4_init_diag(raw_allurl_count)

    # ── __init__ helpers ───────────────────────────────────────────────────

    def _init_basic_options(
        self, nogif, notag, notime, create_dir, download_path, cookies, agent,
        download_time, no_R18G_dir, no_R18_dir, single_thread_mode, stats_collector,
    ):
        """Plain attribute assignments and cookie field unpacking."""
        self.nogif = nogif
        self.notime = notime
        self.notag = notag
        self.create_dir = create_dir
        self.download_path = download_path
        (self.cookie_entries, self.cookie_pool,
         self._cookie_alias_map, self.cookies) = init_cookie_fields(cookies)
        self._pid_cookie_selection = {}
        self._current_account_local = threading.local()
        # Per-thread reserved timetag block (combined concurrent mode). When a
        # worker reserves a contiguous block before a PID's pages, _jpg_advance_
        # timetag hands out base+0, base+1, ... so one PID's pages keep
        # contiguous (non-interleaved) timetags even while other PIDs download
        # concurrently. Unset -> the legacy global +1s path (sequential modes).
        self._timetag_block_local = threading.local()
        # Serializes _apply_live_settings_if_changed's multi-attribute write so
        # K concurrent download workers (Step 4 pool mode AND combined parallel)
        # can't tear it when the user saves settings mid-run.
        self._live_apply_lock = threading.Lock()
        self.agent = agent
        self.download_time = (download_time if isinstance(download_time, datetime.datetime)
                              else datetime.datetime(1970, 1, 1))
        self.no_R18G_dir = no_R18G_dir
        self.no_R18_dir = no_R18_dir
        self.single_thread_mode = single_thread_mode
        self.ai_gen_dir = False
        self._stats_collector = stats_collector
        self.single_mode_flag = bool(single_thread_mode)

    @staticmethod
    def _resolve_intra_pid_wait_range(raw_min, raw_max):
        """Coerce + clamp the intra-PID wait window. Returns ``(min, max)``."""
        try:
            lo = int(raw_min)
            hi = int(raw_max)
        except Exception:
            lo, hi = 1, 3
        if lo < 0:
            lo = 0
        if hi < lo:
            hi = lo
        return lo, hi

    @staticmethod
    def _load_legacy_pid_cooldown_avg():
        """Read pid_cooldown_avg from settings.json. Used only without a scheduler."""
        try:
            from app.core.settings_store import SettingsStore as _SS
            return int(
                _SS(os.getenv("APPDATA") + r"/pixiv_download/")
                .get_section("performance").get("pid_cooldown_avg", 35)
            )
        except Exception:
            return 35

    # _init_jxl_config moved to step4_jxl_conversion._JXLMixin (file-size
    # refactor). Inherited; still called from __init__ as self._init_jxl_config.

    def _init_filter_state(self, like_num, ban_tag, must_tag, special_like_rules,
                           filename_template="", *,
                           tag_strip_brackets=False,
                           tag_strip_special_chars=False,
                           r18_like_num=0):
        """Normalize tag/like filters and seed the per-PID decision cache."""
        self.like_num = like_num if like_num > 0 else 0
        self.r18_like_num = r18_like_num if r18_like_num > 0 else 0
        self.ban_tag = ban_tag
        self.must_tag = must_tag
        self.special_like_rules = special_like_rules
        self.filename_template = str(filename_template or "").strip()
        self.tag_strip_brackets = bool(tag_strip_brackets)
        self.tag_strip_special_chars = bool(tag_strip_special_chars)
        self._ban_tag_norm = self._normalize_filter_tags(self.ban_tag)
        self._must_tag_norm = self._normalize_filter_tags(self.must_tag)
        self._pid_filter_decision = {}

    def _apply_live_settings_if_changed(self):
        """Re-pull in-scope settings when settings.json changed since last apply.

        Called at each PID boundary. Refreshes filters, output path/dir flags,
        filename options, waits, no-scheduler cooldown, and JXL options;
        recomputes derived tag-normal forms and clears the per-PID filter
        decision cache so cached decisions re-evaluate under the new filter.
        Deliberately does NOT touch ``self.allurl``: the download queue stays
        fixed, so lowering ``like_num`` mid-run never re-admits already-excluded
        work (the accepted tighten-only semantic).
        """
        live = getattr(self, "_live", None)
        if live is None:
            return
        sig = live.signature()
        # Cheap early-out (GIL-atomic read) — the common no-mid-run-save case,
        # so the lock below is uncontended on the hot path.
        if sig == getattr(self, "_live_sig", None):
            return
        # Serialize the multi-attribute apply: Step 4 pool mode AND combined
        # concurrent mode call this from K worker threads. Without the lock two
        # workers could both pass the signature check on a mid-run 「儲存設定」 and
        # tear the ~25 shared-attr writes / race _pid_filter_decision.clear().
        # Lazy fallback for __new__-constructed instances (tests) that skip
        # __init__; production always sets it in __init__ before any worker runs.
        lock = getattr(self, "_live_apply_lock", None)
        if lock is None:
            lock = self._live_apply_lock = threading.Lock()
        with lock:
            if sig == getattr(self, "_live_sig", None):
                return  # another worker already applied this signature
            self._apply_live_settings_locked(live, sig)

    def _apply_live_settings_locked(self, live, sig):
        """Apply the new live settings; caller holds ``_live_apply_lock`` and has
        double-checked the signature."""
        s = live.sections()
        self._live_sig = sig
        dl = s.get("download", {}) or {}
        flt = s.get("filter", {}) or {}
        directory = s.get("directory", {}) or {}
        perf = s.get("performance", {}) or {}
        jxl = s.get("jxl", {}) or {}

        # filters (tighten-only for Step 4: the queue is already built)
        self._apply_live_filter_fields(dl)
        self.special_like_rules = list(dl.get("special_like_rules", []) or [])
        self.nogif = bool(flt.get("nogif", False))
        self.notag = bool(flt.get("notag", False))
        self.notime = bool(flt.get("notime", False))
        # download_time is deliberately NOT re-applied mid-run: stamps are
        # pre-allocated per PID up front (assign_pid_timetags) and immutable for
        # the run, so a live edit can't (and shouldn't) change already-assigned
        # stamps — it is picked up at the next run's construction instead. (The
        # old re-apply rewound the cursor to the stale file value because the
        # up-front emit advances ahead of the GUI's write-back.)

        # output location (user opted in despite the split-output tradeoff)
        new_path = str(dl.get("path", "") or "").strip()
        if new_path:
            self.download_path = new_path
        self.create_dir = bool(directory.get("create_dir", False))
        self.no_R18G_dir = bool(directory.get("no_R18G_dir", False))
        self.no_R18_dir = bool(directory.get("no_R18_dir", False))
        self.ai_gen_dir = bool(directory.get("ai_gen_dir", False))

        # filename
        self.filename_template = str(dl.get("filename_template", "") or "").strip()
        self.set_file_mtime = bool(dl.get("set_file_mtime", True))
        self.tag_strip_brackets = bool(dl.get("tag_strip_brackets", False))
        self.tag_strip_special_chars = bool(dl.get("tag_strip_special_chars", False))

        # waits + no-scheduler cooldown fallback
        self.intra_pid_wait_min, self.intra_pid_wait_max = self._resolve_intra_pid_wait_range(
            perf.get("intra_pid_wait_min", 5), perf.get("intra_pid_wait_max", 15)
        )
        try:
            self._legacy_pid_cooldown_avg = int(perf.get("pid_cooldown_avg", 35))
        except (TypeError, ValueError):
            self._legacy_pid_cooldown_avg = 35

        # jxl
        self.jxl_enable = bool(jxl.get("enable", False))
        self.jxl_skip_gif = bool(jxl.get("skip_gif", True))
        try:
            self.jxl_effort = max(1, min(9, int(jxl.get("effort", 7))))
        except (TypeError, ValueError):
            self.jxl_effort = 7
        self.jxl_delete_original = bool(jxl.get("delete_original", False))
        self.jxl_cjxl_path = self._resolve_cjxl_path(str(jxl.get("cjxl_path", "") or ""))

        # re-evaluate cached per-PID filter decisions under the new filter
        try:
            self._pid_filter_decision.clear()
        except Exception:
            self._pid_filter_decision = {}

    def _init_step4_paths_and_state(self):
        """Initialize file paths and per-run mutable Step 4 state."""
        self.url_meta = {}
        self.url_meta_path = os.path.join(self.path, "all_url_meta.json")
        self.allurl = []
        self.exist_json_path = os.path.join(self.path, "exist_pid.json")
        self.legacy_exist_json_path = os.path.join(self.path, "exist.json")
        self.exist_txt_path = os.path.join(self.path, "existPID.txt")

        self.q = Queue()
        self._stop_after_group = False
        self._stopped_by_request = False
        self._active_group_pid = None
        self._attempted_urls = set()
        self._attempted_urls_lock = threading.Lock()
        # URLs whose page is CONFIRMED on disk (a genuine download success or an
        # already-existing skip). Only these are marked 'downloaded' in the DB
        # and dropped from the remaining-to-download set. A URL that was merely
        # *attempted* but then failed (network-retry exhausted) or was cut short
        # by a user Stop is deliberately NOT in this set, so it stays 'pending'
        # and is re-queued next run instead of being silently lost.
        self._completed_urls = set()
        self._completed_urls_lock = threading.Lock()
        # Per-PID author-folder id cache for create_dir (filled from metadata,
        # never a live HTTP call). See _resolve_author_dir_id.
        self._author_dir_cache = {}
        # Serializes read-modify-write of self.url_meta + the JSON flush. With
        # 4 ThreadPoolExecutor workers in _execute_downloads() racing against
        # _mark_gif_cookie_usage / _persist_url_meta, an unguarded dict mutation
        # plus json.dumps could observe a half-updated dict and lose the
        # racing thread's mutation.
        self._url_meta_lock = threading.RLock()
        self._pid_cookie_used = {}
        self._task_filter_stats = {}
        self._step4_filter_skip_counts = {"tag": 0, "like": 0, "no_meta": 0}
        self._step4_filter_skip_notice_emitted = False
        self._step4_filter_skip_every = 200
        self._cookie_usage_counts = {"step4": {}}
        self._cookie_usage_seen = {"step4": set()}

    def _load_initial_exist_pid_set(self):
        """Compute the 'do not redownload' PID set.

        Reads from the SQLite ``v_closed_artworks`` view: complete artworks
        (meta + every page on disk), Pixiv-revoked artworks, and legacy
        imports with no pending work. Critically this view excludes
        partial-download PIDs (some pages on disk, some still pending) so
        Bug 2 — failed pages becoming permanently invisible because the
        PID looked "downloaded" — cannot recur.

        Externally-dropped files (added to download_path outside the app)
        get picked up by ``_sync_exist_pid_from_download_folder`` before
        Step 3, which updates the DB. We deliberately do *not* run a disk
        scan here — that scan was the original source of Bug 2.
        """
        try:
            db = getattr(self, "_metadata_db", None)
            return db.closed_artwork_set() if db is not None else set()
        except Exception:
            return set()

    def _warn_if_meta_empty_with_like_filter(self):
        """Surface a clear warning when meta is missing/empty AND a like filter
        is configured — otherwise step 4 silently flags every PID as "no_meta"
        and the user sees an empty download phase."""
        db = getattr(self, "_metadata_db", None)
        has_meta = bool(self.url_meta) or (
            db is not None and _safe_meta_count(db) > 0
        )
        if has_meta:
            return
        if int(self.like_num or 0) <= 0 and not self.special_like_rules:
            return
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output",
                "<p><font color='red'>[警告] all_url_meta.json 為空，"
                "且設定了愛心過濾門檻；步驟 4 將嘗試即時補抓 meta，"
                "若仍失敗會被全部標為「無meta」跳過。建議先重跑步驟 3。</font></p>"
            ))

    def _sql_like_min(self) -> int:
        """Compute the conservative SQL-level like threshold.

        When special_like_rules are present, a rule may allow a *lower* threshold
        for certain tags.  We use min(like_num, min(rule.min_like)) so the SQL
        pre-filter never incorrectly excludes a PID that would pass a special rule.
        If any rule has min_like=0, effective_min becomes 0 (no SQL likes filter).
        """
        base = int(self.like_num or 0)
        if base <= 0:
            return 0
        rules = getattr(self, "special_like_rules", None) or []
        if not rules:
            return base
        try:
            rule_mins = [
                int(r.get("min_like", 0) or 0)
                for r in rules
                if isinstance(r, dict)
            ]
            if rule_mins:
                return min(base, min(rule_mins))
        except Exception:
            pass
        return 0  # conservative: skip SQL likes filter on error

    def _read_all_url_file_into_state(self):
        """Read pending URLs into ``self.allurl`` from the canonical DB."""
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            try:
                if db.url_row_count() == 0:
                    url_file = os.path.join(self.path, "all_url.txt")
                    if os.path.isfile(url_file):
                        n = db.import_pending_urls_from_file(url_file)
                        self._q.put(WorkerEvent("output",
                            f"<p><font color='gray'>[Migration] 從 all_url.txt 匯入 {n} 筆 URL 至 DB</font></p>"))
                like_min = self._sql_like_min()
                rows = db.get_pending_urls_filtered(like_min=like_min)
                self.allurl = [url for url, _ in rows]
                self._q.put(WorkerEvent("output",
                    f"<p><font color='gray'>從 DB 讀取 {len(self.allurl)} 筆待下載 URL"
                    f"（SQL 預篩：exist_pid 已排除"
                    f"{f'、like<{like_min} 已排除' if like_min > 0 else ''}）</font></p>"))
                self._enqueue_retriable_failures(db)
                return
            except Exception:
                pass
        # Fallback: file (no DB available)
        try:
            print("正在讀取 all_url.txt...", self.path + r"/all_url.txt")
            with open(self.path + r"/all_url.txt") as file:
                self.allurl = [line.rstrip() for line in file if line.rstrip()]
            print(f"all_url.txt 讀取完成，URL數量：{len(self.allurl)}")
            print("正在過濾已存在的 PID...")
        except Exception:
            self._q.put(WorkerEvent("finished", i18n.t("log.dl.done")))

    def _enqueue_retriable_failures(self, db) -> None:
        """Append URLs from prior failed pages back into ``self.allurl``.

        Each retried page is flipped to ``status='pending'`` so the rest of
        the pipeline (filter, download, success/fail marking) is unaware of
        the auto-retry. ``mark_page_pending`` preserves ``attempt_count`` and
        ``last_attempted_at`` via COALESCE, so the cooldown check on the
        next run remains accurate if the retry fails again.
        """
        try:
            from app.core.pid_filesystem import parse_pid_and_page_from_url
            retry_rows = db.get_retriable_failed_pages()
        except Exception:
            return
        if not retry_rows:
            return
        retry_urls: list[str] = []
        for url, pid in retry_rows:
            self._requeue_failed_page(db, url, pid, parse_pid_and_page_from_url)
            retry_urls.append(url)
        if not retry_urls:
            return
        self.allurl.extend(retry_urls)
        self._q.put(WorkerEvent("output",
            f"<p><font color='gray'>{len(retry_urls)} 筆 URL 自先前失敗紀錄重新加入下載佇列</font></p>"))

    @staticmethod
    def _requeue_failed_page(db, url, pid, parser) -> None:
        """Flip one failed page row back to pending; tolerate any single-row error."""
        try:
            parsed_pid, pidx = parser(str(url))
            target_pid = parsed_pid or pid
            if target_pid is None or pidx is None:
                return
            db.mark_page_pending(target_pid, pidx, url=str(url))
        except Exception:
            pass

    def _emit_step4_init_diag(self, raw_allurl_count):
        """Append a step4_init record with the post-filter task summary."""
        self._diag(
            "step4_init",
            raw_all_url_count=raw_allurl_count,
            filtered_all_url_count=self.pid_max,
            exist_pid_count=len(self.exist_pid),
            url_meta_count=_safe_meta_count(getattr(self, "_metadata_db", None)),
            like_min=int(self.like_num or 0),
            special_like_rule_count=len(self.special_like_rules),
            ai_gen_dir=bool(self.ai_gen_dir),
            ban_tag_count=len(self._ban_tag_norm),
            must_tag_count=len(self._must_tag_norm),
            task_filter_stats=self._task_filter_stats if isinstance(self._task_filter_stats, dict) else {},
            single_mode=bool(self.single_mode_flag),
        )

    def _diag(self, event, **fields):
        with contextlib.suppress(Exception):
            append_diagnostic_event(self.path, event, stage="step4", **fields)

    def _count_text_lines(self, file_path):
        return count_text_lines(file_path)

    def _write_all_url_file(self, urls, reason="unknown"):
        return write_all_url_file(self.path, urls, reason=reason, diag=self._diag)

    def _normalize_pixiv_info(self, info):
        """Normalize Pixiv_info result to (tag, like, pagecount, img_url)."""
        try:
            if isinstance(info, list) and len(info) >= 4:
                tag = info[0] if isinstance(info[0], list) else []
                like = info[1]
                pagecount = info[2]
                img_url = info[3]
                return tag, like, pagecount, img_url
        except Exception:
            pass
        return None

    def _to_int(self, value, default=None):
        return to_int_lenient(value, default)

    def _normalize_filter_tags(self, tags):
        return normalize_filter_tags(tags)

    # _normalize_artwork_tags / _tag_hit / _is_r18g_artwork / _is_r18_artwork /
    # _is_ai_artwork moved to step4_filters._Step4FiltersMixin (file-size
    # refactor). Inherited; still reachable as self._is_r18_artwork etc.

    def _resolve_author_dir_id(self, pid):
        """Author-folder id from already-known metadata — NO live HTTP call.

        create_dir used to call pixiv_api.userId() per page: an unauthenticated,
        un-proxied GET that leaked the local IP (defeating the proxy binding),
        ran unthrottled, and returned the junk '(404, 404, 404, 404)' tuple for
        deleted works (which then became a literal directory name). The author is
        already known from Step 2/3 (url_meta in-memory, or artworks.user_id in
        the DB), so read it from there and cache per PID. Returns '' when the
        author is unknown (caller buckets the file directly under download_path).
        """
        pid_key = normalize_pid(pid) or str(pid)
        cache = self._author_dir_cache
        if pid_key in cache:
            return cache[pid_key]
        uid = ""
        meta = self._get_meta(pid_key)
        if isinstance(meta, dict):
            uid = str(meta.get("user_id") or "").strip()
        if not uid:
            db = getattr(self, "_metadata_db", None)
            if db is not None:
                with contextlib.suppress(Exception):
                    uid = str((db.user_id_map_for_pids([pid_key]) or {}).get(pid_key) or "").strip()
        cache[pid_key] = uid
        return uid

    def _resolve_download_target_dir(self, tag, pid, media_kind=None):
        if self.create_dir:
            uid = self._resolve_author_dir_id(pid)
            base_dir = os.path.join(self.download_path, uid) if uid else self.download_path
        else:
            base_dir = self.download_path
        if media_kind:
            base_dir = os.path.join(base_dir, media_kind)
        if self.ai_gen_dir and self._is_ai_artwork(tag):
            base_dir = os.path.join(base_dir, "AI生成")
        if (not self.no_R18G_dir) and self._is_r18g_artwork(tag):
            base_dir = os.path.join(base_dir, "R-18G")
        elif (not self.no_R18_dir) and self._is_r18_artwork(tag):
            base_dir = os.path.join(base_dir, "R-18")
        os.makedirs(base_dir, exist_ok=True)
        return base_dir

    # The step4 filter-skip bookkeeping (_bump_step4_skip_count /
    # _step4_skip_total / _maybe_emit_step4_skip_notice /
    # _maybe_emit_step4_skip_summary / _record_step4_filter_skip /
    # _emit_step4_filter_skip_final_summary) and the cookie-requirement /
    # meta-for-filter fetch + _passes_pid_filter pipeline moved to
    # step4_filters._Step4FiltersMixin (file-size refactor). Inherited unchanged.

    def _has_any_cookie(self):
        if self.cookie_pool:
            return True
        return bool(self.cookies and str(self.cookies).strip())

    def _resolve_pid_and_cookie(self, url, *, source="step4"):
        """Extract PID, pick a cookie, resolve cookie requirement.

        Shared head of ``gif_download`` / ``jpg_download``. Records cookie
        usage under ``source`` and consults ``self.url_meta`` then
        ``pixiv_api.get_pixiv_cookie_requirement`` to decide ``need_cookie``.
        Returns ``(pid, pid_cookie, need_cookie)``.
        """
        pid_candidate = str(url).rsplit('/', 1)[1]
        m = re.match(r"^(\d+)", pid_candidate)
        pid = m.group(1) if m else pid_candidate.rsplit('_', 1)[0]
        pid_cookie = self._select_cookie_for_pid(pid)
        self._record_cookie_usage(source, pid, pid_cookie)
        need_cookie = None
        try:
            meta = self._get_meta(str(pid))
            if meta:
                need_cookie = meta.get('requires_cookie', None)
            if need_cookie is None:
                need_cookie = pixiv_api.get_pixiv_cookie_requirement(pid)
        except Exception:
            need_cookie = None
        return pid, pid_cookie, need_cookie

    def _load_artwork_metadata(self, pid, pid_cookie):
        """Return ``(tag, like, pagecount, img_url)`` or ``None``.

        Prefers ``self.url_meta`` (populated in step 3) so we skip the heavy
        ``Pixiv_info`` HTTP call on cache hit. Falls back to ``Pixiv_info``
        when the cache is empty or lacks the artwork shape (no ``tag`` key).
        Both ``gif_download`` and ``jpg_download`` share this lookup.
        """
        meta = self._get_meta(str(pid))
        if meta and 'tag' in meta:
            return (
                meta.get('tag', []),
                meta.get('like', 0),
                meta.get('pagecount', 1),
                meta.get('img_url', None),
            )
        if pid_cookie:
            info = pixiv_api.Pixiv_info('https://www.pixiv.net/artworks/' + pid, self.agent, cookie=pid_cookie)
        else:
            info = pixiv_api.Pixiv_info('https://www.pixiv.net/artworks/' + pid, self.agent)
        if info == [404]:
            db = getattr(self, "_metadata_db", None)
            if db is not None:
                with contextlib.suppress(Exception):
                    db.mark_artwork_revoked(pid)
        return self._normalize_pixiv_info(info)

    def _build_artwork_headers(self, pid, pid_cookie, need_cookie, *, honour_pid_used=False):
        """Compose request headers for an artwork download.

        ``honour_pid_used=True`` (gif's second fetch) also injects the cookie
        when ``self._pid_cookie_used`` already records a cookied attempt for
        this PID.
        """
        headers = {
            'User-Agent': self.agent,
            'Referer': 'http://www.pixiv.net/' + str(pid),
        }
        used_pid = self._pid_cookie_used.get(str(pid), False) if honour_pid_used else False
        if (need_cookie is True or used_pid) and pid_cookie:
            headers['Cookie'] = pid_cookie
        return headers

    def _log_ugoira_meta_failure(self, pid, htmlfile, meta_trace, first_try_resp):
        """Diagnostic dump when ugoira_meta returns non-200.

        Extracted from ``gif_download`` to keep the main flow flat.
        """
        print(
            f"[pixiv_thread] PID {pid} failed ugoira_meta, "
            f"first_try_status={meta_trace.get('first_try_status')}, "
            f"retry_used={meta_trace.get('retry_used')}, "
            f"retry_with_cookie_status={meta_trace.get('retry_with_cookie_status')}, "
            f"final_status={htmlfile.status_code}"
        )
        if first_try_resp is not None:
            with contextlib.suppress(Exception):
                print(f"[pixiv_thread] response preview (first): {first_try_resp.text[:500]}")
        try:
            stage = "retry" if meta_trace.get("retry_used") else "first"
            print(f"[pixiv_thread] response preview ({stage}): {htmlfile.text[:500]}")
        except Exception:
            pass

    # The meta-for-filter fetch fallbacks (_fetch_filter_meta_via_scheduler /
    # _fetch_filter_meta_direct / _build_filter_meta_entry /
    # _fetch_meta_for_filter), the per-PID filter predicates
    # (_filter_no_meta_decision / _filter_blocked_by_ban_tag /
    # _filter_missing_must_tag / _filter_below_like_threshold), and the
    # _record_filter_decision / _passes_pid_filter pipeline moved to
    # step4_filters._Step4FiltersMixin (file-size refactor). Inherited unchanged.

    # Legacy constructor-arg coercion (_cast_or_skip, _apply_legacy_* and the
    # _LEGACY_*_SCHEMA constants) moved to step4_legacy_args._Step4LegacyArgsMixin
    # (file-size refactor). Inherited unchanged, still reachable as
    # download_thread._apply_legacy_constructor_args / ._LEGACY_SCALAR_KW_SCHEMA.

    # The JXL background-conversion subsystem (_resolve_cjxl_path,
    # _build_jxl_command, _run_cjxl_*, _jxl_*, _convert_file_to_jxl,
    # _start_jxl_worker_if_needed, _jxl_worker_loop, _enqueue_jxl,
    # _discard_pending_jxl_items, _drain_jxl_queue + _JXL_SUPPORTED_EXTS)
    # moved to step4_jxl_conversion._JXLMixin (file-size refactor).
    # download_thread inherits the mixin, so they stay reachable as
    # self._enqueue_jxl / self._drain_jxl_queue etc.

    # _normalize_ugoira_frames / _save_ugoira_gif moved to
    # step4_media._Step4MediaMixin (file-size refactor). Inherited unchanged.

    # _normalize_tag_for_filename / _build_hashtag_text / _split_timetag /
    # _filename_template_fields / _render_template_filename /
    # _render_default_filename / _build_download_filename moved to
    # step4_filename._FilenameMixin (file-size refactor). Inherited unchanged.

    # Pacing / countdown helpers (_format_size_human, _emit_countdown_start_log,
    # _countdown_tick, _run_download_countdown, _sleep_between_downloads,
    # _sleep_within_pid, _calc_sleep_delay) moved to
    # step4_pacing._Step4PacingMixin (file-size refactor). Inherited unchanged.

    def _is_cookie_used_for_pid(self, pid):
        """Return whether this PID download used cookie-protected metadata."""
        try:
            pid_key = str(pid)
            used_map = getattr(self, '_pid_cookie_used', {})
            if isinstance(used_map, dict) and pid_key in used_map:
                return bool(used_map.get(pid_key))
            # 否則回退讀取 all_url_meta.json 的 requires_cookie
            meta = self._get_meta(pid_key)
            req = meta.get('requires_cookie', None)
            if req is True:
                return self._has_any_cookie()
            if req is False:
                return False
            return False
        except Exception:
            return False

    def _is_pid_cached_meta(self, pid):
        """Return True when this PID has usable cached metadata in all_url_meta."""
        try:
            pid_key = normalize_pid(pid) or str(pid)
            meta = self._get_meta(pid_key)
            if not meta:
                return False
            img_url = meta.get("img_url")
            pagecount = meta.get("pagecount", 0)
            if img_url in (None, "", "None"):
                return False
            try:
                return int(pagecount or 0) > 0
            except Exception:
                return False
        except Exception:
            return False

    def _extract_pid_from_download_url(self, url):
        try:
            return str(url).rsplit('/', 1)[1].split('_', 1)[0]
        except Exception:
            return None

    def _extract_page_from_download_url(self, url):
        try:
            filename = str(url).rsplit('/', 1)[1]
            m = re.search(r"_(?:p|ugoira)(\d+)\.[A-Za-z0-9]+$", filename, flags=re.IGNORECASE)
            if not m:
                return None
            return int(m.group(1))
        except Exception:
            return None

    def _resolve_download_url(self, stored_url):
        """
        all_url may store canonical URL without hash segment.
        Rebuild actual pximg URL from all_url_meta.img_url before download.
        """
        try:
            u = str(stored_url).strip()
            if not u:
                return u
            name = u.rsplit("/", 1)[1]
            m = re.match(
                r"^(?P<pid>\d{5,12})(?P<hash>-[a-f0-9]+)?_(?P<kind>p|ugoira)(?P<idx>\d+)\.(?P<ext>[A-Za-z0-9]+)$",
                name,
                re.IGNORECASE,
            )
            if not m:
                return u
            if m.group("hash"):
                return u
            pid = normalize_pid(m.group("pid"))
            idx = m.group("idx")
            meta = self._get_meta(pid)
            img_url = str(meta.get("img_url", "")).strip()
            if not img_url or "." not in img_url:
                return u
            left, right = img_url.rsplit(".", 1)
            # Support both "..._p.jpg" and legacy "..._p0.jpg" style.
            left = re.sub(r"_(p|ugoira)\d+$", r"_\1", left, flags=re.IGNORECASE)
            if not re.search(r"_(p|ugoira)$", left, flags=re.IGNORECASE):
                return u
            rebuilt = f"{left}{idx}.{right}"
            return rebuilt
        except Exception:
            return str(stored_url)

    # _new_step4_filter_stats / _classify_url_for_filter / _bump_filter_reason /
    # _prepare_download_tasks / _read_pictures_id_set / _requeue_no_meta_pids
    # live on step4_filters._Step4FiltersMixin (file-size refactor); the
    # duplicate copies that used to shadow them here were removed. Inherited
    # unchanged.

    def _group_urls_by_pid(self, urls):
        groups = {}
        order = []
        for u in urls:
            pid = self._extract_pid_from_download_url(u)
            if not pid:
                continue
            if pid not in groups:
                groups[pid] = []
                order.append(pid)
            groups[pid].append(u)
        return order, groups

    def _reorder_pid_order_by_author(self, pid_order):
        """Reorder pid_order so same-author works are contiguous.

        Returns ``(flat_order, author_batches)``. Falls back to a single
        batch (current behavior) when no metadata DB is available."""
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return list(pid_order), [list(pid_order)]
        try:
            uid_map = db.user_id_map_for_pids(pid_order)
        except Exception:
            return list(pid_order), [list(pid_order)]
        flat_order, author_batches = compute_author_order(pid_order, uid_map)
        unknown_count = sum(
            1 for p in pid_order if not str(uid_map.get(p) or "").strip()
        )
        if unknown_count:
            self._q.put(WorkerEvent("output",
                f"<p><font color='orange'>[作者排序] {unknown_count} 筆作品作者不明，"
                f"已排到最後；重跑步驟 2/3 可補作者資料</font></p>"))
        return flat_order, author_batches

    def _resolve_execution_order(self, pid_order):
        """Decide final PID sequence + per-author batches for the executors.

        author_order off -> (pid_order unchanged, single batch) [zero regression]
        author_order on  -> author-grouped reorder
        """
        if getattr(self, "author_order", False):
            return self._reorder_pid_order_by_author(pid_order)
        return list(pid_order), [list(pid_order)]

    _low_disk_notified = False

    def _check_disk_space_or_stop(self) -> bool:
        """Boundary guard: stop the run when the download drive is nearly full.

        Returns False (and sets the stop event, notifying the user once) when
        free space is below LOW_DISK_MIN_FREE_BYTES; True otherwise. Never
        raises — an unreadable path must not break downloads.
        """
        try:
            free = shutil.disk_usage(self.path).free
        except Exception:
            return True
        if free >= LOW_DISK_MIN_FREE_BYTES:
            return True
        if not self._low_disk_notified:
            self._low_disk_notified = True
            with contextlib.suppress(Exception):
                self._q.put(WorkerEvent(
                    "output",
                    f"<p><font color='red'>下載磁碟空間不足（剩餘 "
                    f"{free // (1024 * 1024)} MB，低於 "
                    f"{LOW_DISK_MIN_FREE_BYTES // (1024 * 1024)} MB），"
                    f"已停止下載。請清出空間後再重新執行。</font></p>",
                ))
        self.stop()
        return False

    def _download_pid_group(self, pid, urls):
        if not self._check_disk_space_or_stop():
            return []  # stop already set; pages stay pending for the next run
        # Pick up any mid-run 「儲存設定」 before this PID's pages: path/dir
        # flags, filename options, waits, cooldown, and JXL options.
        self._apply_live_settings_if_changed()
        failed = []
        # Build session with the bound proxy when an account is currently held.
        acc = self._current_download_account()
        if acc is not None:
            from app.core import pixiv_api as _pixiv_api
            sess = _pixiv_api.make_session(acc.proxy_url)
        else:
            sess = requests.Session()
        # Reserve ONE shared timetag for this PID so every page gets the same
        # timestamp / filename prefix / mtime (an artwork = one moment). Pre-
        # allocated by assign_pid_timetags (keyed on pid), so this is an O(1)
        # lookup with no lock. Single owner of the per-PID block for every Step 4
        # path (single / pool / scheduler) and combined mode — all funnel here.
        self._begin_pid_timetag_block(pid)
        try:
            with diag_log.span(diag_log.DOWNLOAD, f"PID {pid} 下載 ({len(urls)} 頁)"):
                for idx, u in enumerate(urls):
                    ret = self.gif_or_jpg(u, session=sess)
                    if ret == -1:
                        # 已存在或被規則略過，不視為失敗
                        diag_log.log(diag_log.DOWNLOAD, f"PID {pid} p{idx} 跳過(已存在/規則)")
                    elif ret is None:
                        failed.append([u, "unknown"])
                        diag_log.log(diag_log.DOWNLOAD, f"PID {pid} p{idx} 失敗(unknown)")
                    elif ret != 0:
                        failed.append(ret)
                        diag_log.log(diag_log.DOWNLOAD, f"PID {pid} p{idx} 失敗")
                    else:
                        diag_log.log(diag_log.DOWNLOAD, f"PID {pid} p{idx} 完成")
                    if idx < len(urls) - 1 and not self._stop_event.is_set():
                        # 同一 PID 多頁時，頁面間做短暫休眠；已存在/略過(-1)
                        # 也要節流，否則多頁作品會瞬間掃過。
                        self._sleep_within_pid(pid)
                    if self._stop_event.is_set():
                        break
        finally:
            with contextlib.suppress(Exception):
                self._end_pid_timetag_block()
            with contextlib.suppress(Exception):
                sess.close()
        return failed

    # PID-from-filename parsers, splitID, and get_filelist moved to
    # step4_folder_list._Step4FolderListMixin (file-size refactor). Inherited
    # unchanged.

    def _emit_phase(self, text: str) -> None:
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("phase", text))

    def _emit_timechanged(self) -> None:
        """Push the advanced download_time so the GUI persists it.

        Emitted once up front by ``assign_pid_timetags`` (which advances the
        cursor past the whole pre-allocated block) and again at Step 4 finalize
        to capture any lazy-fallback straggler advance. Since the cursor is
        persisted before downloads start, a crash mid-run can never reuse stamps.
        """
        with self.timelock:
            value = self.download_time.strftime('%Y-%m-%d %H:%M:%S')
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("timechanged", value))

    def _emit_step4_header(self):
        self._q.put(WorkerEvent("output", f"<p><font color='red'>{i18n.t('log.dl.start')}</font></p>"))
        self._q.put(WorkerEvent("output", f"<p><font color='gray'>{i18n.t('log.dl.pending', n=len(self.allurl))}</font></p>"))
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[Step4過濾設定] like_min={self.like_num}, special_rules={len(self.special_like_rules)}, ban_tag={len(self._ban_tag_norm)}, must_tag={len(self._must_tag_norm)}, ai_dir={bool(self.ai_gen_dir)}</font></p>"
            ))
        if self.jxl_enable:
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>JXL 轉檔啟用：cjxl='{self.jxl_cjxl_path}' effort={self.jxl_effort} delete_original={self.jxl_delete_original}</font></p>"
            ))
        try:
            stats = self._task_filter_stats if isinstance(self._task_filter_stats, dict) else {}
            self._q.put(WorkerEvent("output",
                "<p><font color='gray'>[TaskFilter][Step4] input={}, skipped_exist={}, skipped_like={}, skipped_tag={}, skipped_no_meta={}, duplicate={}, invalid={}, pending={}</font></p>".format(
                    stats.get('input_count', len(self.allurl)),
                    stats.get('skipped_exist_count', 0),
                    stats.get('skipped_like_count', 0),
                    stats.get('skipped_tag_count', 0),
                    stats.get('skipped_no_meta_count', 0),
                    stats.get('duplicate_count', 0),
                    stats.get('invalid_count', 0),
                    stats.get('output_count', len(self.allurl)),
                )
            ))
        except Exception:
            pass

    def _handle_zero_pending(self):
        self._diag(
            "step4_no_pending_skip_rewrite",
            reason="filtered_to_zero_before_download",
            filter_stats=self._task_filter_stats if isinstance(self._task_filter_stats, dict) else {},
        )
        self._emit_step4_filter_skip_final_summary()
        self._emit_cookie_usage_summary("step4", "Step4 Cookie統計")
        self._emit_output("<p><font color='orange'>Step4 無待下載 URL，保留現有 all_url.txt 不改寫</font></p>")
        if self._stopped_by_request or self._stop_event.is_set():
            self._q.put(WorkerEvent("finished", i18n.t("log.dl.stopped")))
            self._q.put(WorkerEvent("next", -1))
        else:
            self._q.put(WorkerEvent("finished", i18n.t("log.dl.done")))

    def _emit_single_mode_header(self):
        """Print the 'single-thread mode' header + cooldown summary line."""
        self._q.put(WorkerEvent("output",
            "<p><font color='green'>下載模式：單執行緒 + 每個 PID 共用單一 Session</font></p>"))
        inter_pid_desc = ("由排程器管理（單帳號平均冷卻）"
                          if self._scheduler is not None
                          else "固定冷卻（pid_cooldown_avg）")
        self._q.put(WorkerEvent("output",
            f"<p><font color='green'>同PID等待: {self.intra_pid_wait_min}~"
            f"{self.intra_pid_wait_max} 秒；PID間: {inter_pid_desc}</font></p>"))

    def _current_download_account(self):
        """The account bound to the calling worker thread (or None).

        Thread-local: pool mode runs 4 concurrent PID workers, each with its
        own account; combined mode sets/clears it on the combined thread via
        the same interface.
        """
        return getattr(self._current_account_local, "account", None)

    def _set_current_download_account(self, account):
        self._current_account_local.account = account

    def _clear_current_download_account(self):
        with contextlib.suppress(AttributeError):
            del self._current_account_local.account

    def _download_pid_with_scheduler(self, pid, urls):
        """Acquire an account, run download under retry, release. Returns (result, stop).

        Release semantics:
        - retry exhausted on the network triple -> release(ok=False), the
          scheduler disables the cookie/proxy for the run;
        - user stop (before/during the retry waits) or a non-network
          exception (disk/decode error — not the account's fault) ->
          release_neutral(), the cookie is neither disabled nor credited
          with a success (no last_tested_at refresh);
        - clean run -> release(ok=True).
        """
        acc = self._acquire_account()
        if acc is None:
            return [], True  # stop signal or all disabled
        pid_key = normalize_pid(pid) or str(pid)
        self._pid_cookie_selection[pid_key] = acc.cookie  # sticky cookie for this PID
        ok = False
        neutral = False
        try:
            self._set_current_download_account(acc)
            self._emit_phase(i18n.t("log.phase.downloading", pid=pid))
            ok, result, _ = self._run_with_network_retry(
                f"PID {pid}",
                lambda: self._download_pid_group(pid, urls),
            )
            return (result if isinstance(result, list) else []), False
        except Exception:
            neutral = True
            raise
        finally:
            self._clear_current_download_account()
            if not ok and self._stop_event.is_set():
                neutral = True
            if neutral and self._scheduler is not None:
                self._scheduler.release_neutral(acc)
            else:
                # One full avg for the PID pickup + a flat 5 s per page
                # (PAGE_COOLDOWN_SEC): a multi-page PID rests a bit longer
                # without sidelining the cookie for pages × avg.
                self._release_account(acc, ok=ok, pages=len(urls))

    def _execute_downloads_single(self, pid_order, pid_groups):
        """Single-thread per-PID-group download loop with optional scheduler."""
        self._emit_single_mode_header()
        failed_nested = []
        for idx, pid in enumerate(pid_order, start=1):
            if self._stop_after_group:
                self._emit_output("<p><font color='orange'>收到中止要求：已在上一個 PID 組完成後停止</font></p>")
                break
            if self._stop_event.is_set():
                break
            self._active_group_pid = pid
            self._q.put(WorkerEvent("output",
                f"<p><font color='black'>處理 PID {idx}/{len(pid_order)}：{pid}"
                f"（{len(pid_groups.get(pid, []))} 張）</font></p>"))

            urls = pid_groups.get(pid, [])
            self._emit_phase(i18n.t("log.phase.downloading", pid=pid))
            if self._scheduler is not None:
                result, stop = self._download_pid_with_scheduler(pid, urls)
                if stop:
                    self._active_group_pid = None
                    break
                failed_nested.append(result)
            else:
                failed_nested.append(self._download_pid_group(pid, urls))

            self._active_group_pid = None
            # Bug 1 fix: only mark the PID as downloaded when the whole
            # group succeeded. Previously this ran unconditionally, so
            # multi-page artworks where some pages failed got the entire
            # PID stamped as downloaded — the remaining pages then became
            # invisible to subsequent runs.
            if not failed_nested[-1]:
                self._maybe_flush_exist_pid(pid)
            done = getattr(self, "_step4_pid_done", 0) + 1
            self._step4_pid_done = done
            total = getattr(self, "_step4_pid_total", len(pid_order))
            self._emit_phase(f"步驟 4：下載中（{done} / {total} PID 完成）")
            self._maybe_flush_url_meta_periodically(done)
            if self._stop_event.is_set():
                break
            # _sleep_between_downloads is a no-op when scheduler is set;
            # call unconditionally — the method itself decides whether to sleep.
            if idx < len(pid_order):
                self._sleep_between_downloads(pid)
        return failed_nested

    def _execute_downloads_pool(self, author_batches, pid_groups):
        """Multi-thread per-PID-group download via ThreadPoolExecutor.

        Processes ``author_batches`` one batch (= one author) at a time:
        submit all of a batch's PIDs, drain them (as_completed), then move to
        the next batch. With a single batch this is identical to the previous
        submit-all behavior. Author-order strictness is the per-batch barrier.
        """
        self._q.put(WorkerEvent("output",
            "<p><font color='gray'>下載模式：多執行緒（以 PID 為單位分派；每個 PID 仍共用單一 Session）</font></p>"))
        failed_nested = []
        total = getattr(self, "_step4_pid_total",
                        sum(len(b) for b in author_batches))
        done = 0
        pool_stopped = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as self.executor:
            for batch in author_batches:
                if pool_stopped or self._stop_event.is_set():
                    break
                use_scheduler = self._scheduler is not None
                worker = self._download_pid_with_scheduler if use_scheduler else self._download_pid_group
                futures = [
                    self.executor.submit(worker, pid, pid_groups.get(pid, []))
                    for pid in batch
                ]
                for fu in concurrent.futures.as_completed(futures):
                    try:
                        result = fu.result()
                        if use_scheduler:
                            result, stop = result
                            if stop:
                                # Stop request or all cookies disabled: the PID
                                # was never attempted — don't count it as a
                                # no-failure success, and don't submit further
                                # batches (its URLs stay in the unattempted set
                                # that _compute_remaining_urls preserves).
                                pool_stopped = True
                                continue
                        failed_nested.append(result)
                    except Exception:
                        failed_nested.append([])
                    done += 1
                    self._step4_pid_done = done
                    self._emit_phase(f"步驟 4：下載中（{done} / {total} PID 完成）")
                    self._maybe_flush_url_meta_periodically(done)
        if pool_stopped:
            self._emit_output("<p><font color='orange'>下載池已停止：剩餘 PID 保留為未嘗試，下次執行續傳</font></p>")
        return failed_nested

    def _execute_downloads(self, pid_order, pid_groups, author_batches):
        if self.single_mode_flag:
            return self._execute_downloads_single(pid_order, pid_groups)
        return self._execute_downloads_pool(author_batches, pid_groups)

    @staticmethod
    def _classify_one_fail_item(item):
        """Translate one fail item from a worker into ``(url_text, info_text)`` or None."""
        if item is None:
            return None
        if isinstance(item, (list, tuple)):
            if len(item) >= 2:
                return (str(item[0]), str(item[1]))
            if len(item) == 1:
                return (str(item[0]), "")
            return None
        if isinstance(item, str):
            return (item, "")
        return (str(item), "")

    @staticmethod
    def _classify_download_results(failed_nested):
        """Flatten nested worker results into ``(url_text, info_text)`` fail records.

        Filters out ``0`` sentinels and ``None`` entries; tolerates list/tuple/str
        shapes returned by the per-PID workers.
        """
        flat = [i for item in failed_nested if isinstance(item, list) for i in item if i != 0]
        out = []
        for item in flat:
            record = download_thread._classify_one_fail_item(item)
            if record is not None:
                out.append(record)
        return out

    def _compute_remaining_urls(self, stop_to_download, fail_records):
        """Merge stop-queue URLs, failed http URLs, and unattempted URLs (deduped)."""
        try:
            failed_to_download = [str(url_text) for (url_text, _info) in fail_records if str(url_text).startswith('http')]
        except Exception:
            failed_to_download = []
        try:
            with self._completed_urls_lock:
                completed_snapshot = set(self._completed_urls)
        except Exception:
            completed_snapshot = set()
        # Anything not confirmed-on-disk is still owed: this includes genuinely
        # unattempted URLs AND attempted-but-failed / network-exhausted /
        # stop-interrupted ones, so none of them are silently dropped.
        not_completed_urls = [u for u in self.allurl if u not in completed_snapshot]
        remaining_urls = []
        seen = set()
        for u in stop_to_download + failed_to_download + not_completed_urls:
            if u in seen:
                continue
            seen.add(u)
            remaining_urls.append(u)
        self._diag(
            "step4_remaining_computed",
            stop_queue_count=len(stop_to_download),
            failed_url_count=len(failed_to_download),
            not_completed_count=len(not_completed_urls),
            remaining_count=len(remaining_urls),
            completed_count=len(completed_snapshot),
        )
        return remaining_urls

    # SQLite metadata-DB sync helpers (_init_metadata_db, _emit_metadata_db_stats,
    # _mirror_exist_pid_to_db, _sync_meta_to_db, _meta_to_db_kwargs,
    # _upsert_meta_in_db, _persist_url_meta, _mark_completed_urls_in_db) moved to
    # step4_db_sync._Step4DbSyncMixin (file-size refactor). Inherited unchanged.

    def _finalize_downloads(self, failed_nested):
        fail_records = self._classify_download_results(failed_nested)
        if fail_records:
            err_lines = [f"{url_text} {info_text}" for url_text, info_text in fail_records]
            atomic_write_text(self.path + "/err_url.txt", err_lines, backup=False)
            self._shadow_mark_failures(fail_records)
        self._diag("step4_fail_records", fail_record_count=len(fail_records))
        stop_to_download = []
        while True:
            try:
                stop_to_download.append(self.q.get_nowait())
            except Empty:
                break
        remaining_urls = self._compute_remaining_urls(stop_to_download, fail_records)
        self._mark_completed_urls_in_db()
        self._write_all_url_file(remaining_urls, reason="step4_remaining")
        self._emit_output(f"<p><font color='gray'>已更新 all_url.txt，剩餘 {len(remaining_urls)} 筆待下載</font></p>")
        self._persist_url_meta()
        return remaining_urls

    # _shadow_mark_failures, _maybe_flush_exist_pid, _maybe_flush_url_meta_periodically
    # (+ _STEP4_URL_META_FLUSH_EVERY) and _sync_exist_pid_to_db moved to
    # step4_db_sync._Step4DbSyncMixin (file-size refactor). Inherited unchanged.

    def _emit_step4_summary_and_finalize(self, remaining_urls):
        if self.jxl_enable:
            # Wait for any backlog the background worker still has — counters
            # used in the summary below are written only by that worker, so
            # we must drain before reading.  On user-stop, drain discards
            # pending items so the summary fires promptly.
            if any(t.is_alive() for t in self._jxl_worker_threads):
                self._emit_output("<p><font color='gray'>等待背景 JXL 轉檔完成...</font></p>")
            self._drain_jxl_queue()
            try:
                total_saved = int(self._jxl_src_total_bytes) - int(self._jxl_dst_total_bytes)
                total_ratio = 0.0
                if int(self._jxl_src_total_bytes) > 0:
                    total_ratio = (float(total_saved) / float(self._jxl_src_total_bytes)) * 100.0
                self._q.put(WorkerEvent("output",
                    f"<p><font color='gray'>JXL 結果：成功 {self._jxl_ok_count}、失敗 {self._jxl_fail_count}、總容量 {self._format_size_human(self._jxl_src_total_bytes)} → {self._format_size_human(self._jxl_dst_total_bytes)}（省下 {self._format_size_human(total_saved)}, {total_ratio:.2f}%）</font></p>"
                ))
            except Exception:
                pass
        self._emit_timechanged()
        self._emit_step4_filter_skip_final_summary()
        self._emit_cookie_usage_summary("step4", "Step4 Cookie統計")
        if self._stats_collector is not None:
            self._stats_collector.save()
        if self._stopped_by_request or self._stop_event.is_set():
            self._diag(
                "step4_finished",
                status="stopped",
                remaining_count=len(remaining_urls),
                exist_pid_count=len(self.exist_pid),
            )
            self._q.put(WorkerEvent("finished", i18n.t("log.dl.stopped")))
            self._q.put(WorkerEvent("next", -1))
        else:
            self._diag(
                "step4_finished",
                status="completed",
                remaining_count=len(remaining_urls),
                exist_pid_count=len(self.exist_pid),
            )
            self._q.put(WorkerEvent("finished", i18n.t("log.dl.done")))

    def run(self):
        try:
            # The "本次執行" elapsed timer is reset/resumed by RunController._start_step
            # at run launch (covers combined mode + Step 1/2/3-only runs, and avoids
            # rewinding the whole-run timer when Run All reaches Step 4).
            # Re-check filters in worker thread so Step4 can validate like/tag once
            # without blocking the GUI thread during object construction.
            self._emit_phase("步驟 4：過濾任務清單中...")
            raw_count = len(self.allurl)
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[Step4] 開始過濾任務清單，共 {raw_count} 筆 URL...</font></p>"))
            self.allurl, self._task_filter_stats = self._prepare_download_tasks(self.allurl, allow_network=True)
            self.pid_max = len(self.allurl)
            stats = self._task_filter_stats if isinstance(self._task_filter_stats, dict) else {}
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[Step4] 過濾完成：保留 {self.pid_max} 筆，略過 "
                f"{raw_count - self.pid_max} 筆"
                f"（已存在 {stats.get('skipped_exist_count', 0)}、"
                f"Like 不足 {stats.get('skipped_like_count', 0)}、"
                f"Tag 不符 {stats.get('skipped_tag_count', 0)}）</font></p>"
            ))
            self._diag(
                "step4_run_start",
                pending_url_count=self.pid_max,
                filter_stats=stats,
            )
            self._emit_step4_header()
            if self.pid_max <= 0:
                self._emit_phase("")
                self._handle_zero_pending()
                return
            self._emit_phase("步驟 4：分組 PID 中...")
            self._q.put(WorkerEvent("output",
                "<p><font color='gray'>[Step4] 開始 PID 分組...</font></p>"))
            pid_order, pid_groups = self._group_urls_by_pid(self.allurl)
            if getattr(self, "author_order", False):
                self._emit_phase("步驟 4：依作者排序中...")
            pid_order, author_batches = self._resolve_execution_order(pid_order)
            # Pre-allocate one timetag per PID by queue position (persists the
            # advanced cursor once). Downloads then just look their stamp up.
            self.assign_pid_timetags(pid_order)
            self._diag("step4_grouped", pid_count=len(pid_order), url_count=len(self.allurl))
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[Step4] PID 分組完成：{len(pid_order)} 個 PID、{len(self.allurl)} 個 URL</font></p>"))
            self._emit_phase(f"步驟 4：下載中（0 / {len(pid_order)} PID 完成）")
            self._step4_pid_total = len(pid_order)
            self._step4_pid_done = 0
            failed_nested = self._execute_downloads(pid_order, pid_groups, author_batches)
            self._emit_phase("步驟 4：整理失敗項目...")
            self._q.put(WorkerEvent("output",
                "<p><font color='gray'>[Step4] 下載迴圈結束，整理失敗項目中...</font></p>"))
            remaining_urls = self._finalize_downloads(failed_nested)
            self._emit_phase("步驟 4：更新已下載紀錄...")
            self._q.put(WorkerEvent("output",
                "<p><font color='gray'>[Step4] 寫入已下載 PID 紀錄...</font></p>"))
            self._sync_exist_pid_to_db()
            self._emit_phase("步驟 4：產生摘要...")
            self._emit_step4_summary_and_finalize(remaining_urls)
            self._emit_phase("")
        except Exception as e:
            self._emit_phase("")
            self._diag("step4_exception", error=output_err(e))
            self._q.put(WorkerEvent("output",
                f"<p><font color='red'>{i18n.t('log.dl.fail')}</font></p>"))
            self._q.put(WorkerEvent("output", output_err(e)))
            self._q.put(WorkerEvent("next", -1))
    def _emit_download_progress_log(self, original_url, resolved_url):
        """Print a one-line "[下載] PID X 第 N 張 (image|ugoira)" status."""
        try:
            pid_from_original = normalize_pid(self._extract_pid_from_download_url(original_url))
            pid_from_resolved = normalize_pid(self._extract_pid_from_download_url(resolved_url))
            pid_for_log = pid_from_original or pid_from_resolved or "unknown"
            page = self._extract_page_from_download_url(resolved_url)
            if page is None:
                page = self._extract_page_from_download_url(original_url)
            media = "ugoira" if "ugoira" in str(resolved_url).lower() else "image"
            cookie_used = self._is_cookie_used_for_pid(pid_for_log)
            if page is None:
                self._q.put(WorkerEvent("output",
                    f"<p><font color='black'>[下載] PID {pid_for_log} ({media}, cookie_used={cookie_used})</font></p>"))
            else:
                self._q.put(WorkerEvent("output",
                    f"<p><font color='black'>[下載] PID {pid_for_log} 第 {int(page) + 1} 張 ({media}, cookie_used={cookie_used})</font></p>"))
        except Exception:
            pass

    def _record_attempted_and_advance(self, original_url):
        """Mark a URL as attempted and bump the progress counter.

        Being *attempted* only drives progress display — it does NOT imply the
        page landed on disk. Completion is tracked separately by
        :meth:`_record_completed` so a failed/stopped attempt is never mistaken
        for a download."""
        try:
            with self._attempted_urls_lock:
                self._attempted_urls.add(original_url)
        except Exception:
            pass
        if not self._stop_event.is_set():
            self._q.put(WorkerEvent("progress", (1, self.pid_max)))

    def _record_completed(self, original_url):
        """Record a URL whose page is confirmed on disk.

        Called only on a genuine download success (``ret == 0``) or an
        already-existing skip (``ret == -1``). These are the sole URLs that
        :meth:`_mark_completed_urls_in_db` flips to ``status='downloaded'`` and
        that :meth:`_compute_remaining_urls` drops from the retry set."""
        try:
            with self._completed_urls_lock:
                self._completed_urls.add(original_url)
        except Exception:
            pass

    @staticmethod
    def _extract_pid_from_pximg_url(resolved_url, *, is_ugoira):
        """Pull the bare PID out of a pximg URL. Different shape for ugoira vs jpg."""
        tail = resolved_url.rsplit('/', 1)[1]
        if is_ugoira:
            return normalize_pid(tail.rsplit('_', 1)[0].rsplit('ugoira0')[0])
        return normalize_pid(tail.split('_', 1)[0])

    def _dispatch_download(self, original_url, resolved_url, session, is_ugoira):
        """Choose gif_download vs jpg_download, swap original_url back into the result."""
        pid = self._extract_pid_from_pximg_url(resolved_url, is_ugoira=is_ugoira)
        if pid in self.exist_pid:
            return -1
        if is_ugoira:
            ret = self.gif_download(resolved_url, session=session)
        else:
            ret = self.jpg_download(resolved_url, session=session)
        if isinstance(ret, list) and ret and str(ret[0]) == resolved_url:
            ret[0] = original_url
        return ret

    def gif_or_jpg(self, url, session=None):
        original_url = str(url)
        resolved_url = self._resolve_download_url(original_url)
        self._emit_download_progress_log(original_url, resolved_url)
        self._pause_event.wait()
        self._record_attempted_and_advance(original_url)
        if self._stop_event.is_set():
            # Stop fired before this page was fetched: hand the URL back to the
            # remaining-queue and return the 0 sentinel WITHOUT recording it as
            # completed, so it stays pending and is retried next run.
            self.q.put(original_url)
            return 0
        is_ugoira = 'ugoira' in resolved_url
        try:
            ret = self._dispatch_download(original_url, resolved_url, session, is_ugoira)
        except DownloadStopped:
            # Stop fired mid-stream (inside the body transfer). Settle it
            # exactly like the pre-fetch stop above: requeue and return the 0
            # sentinel WITHOUT _record_completed, so the page stays pending and
            # is retried next run — never recorded done, never marked failed.
            # (The partial .part was already removed by _jpg_stream_to_disk.)
            self.q.put(original_url)
            return 0
        # ret == 0  -> genuine download success; ret == -1 -> already on disk.
        # Both mean the page is present, so they (and only they) count as done.
        # ret is None / a fail list -> not recorded, stays pending for retry.
        if ret == 0 or ret == -1:
            self._record_completed(original_url)
        return ret
    # The per-page media subsystem (cookie-usage stamping:
    # _stamp_step4_gif_cookie_usage / _atomic_write_url_meta_with_raw_fallback /
    # _mark_gif_cookie_usage; ugoira: _stream_ugoira_zip_bytes /
    # _extract_ugoira_frame_blobs / _build_ugoira_save_path /
    # _diag_ugoira_meta_fetch / _maybe_mark_meta_retry_cookie /
    # _parse_ugoira_meta_payload / _fetch_ugoira_meta / gif_download; jpg:
    # _JPG_USER_AGENT / _jpg_advance_timetag / _jpg_extract_page_and_format /
    # _jpg_build_headers / _jpg_resolve_filename / _apply_download_mtime /
    # _jpg_stream_to_disk / _jpg_attempt / jpg_download) moved to
    # step4_media._Step4MediaMixin (file-size refactor). Inherited unchanged.

    def flush_for_shutdown(self):
        """Synchronously persist in-flight metadata and close the SQLite cache.

        Called by the window-close hook in flet_app.py just before
        ``os._exit(0)``. Bypasses the worker's async stop machinery so the
        flush completes even when the process is about to be killed; safe to
        call multiple times (best-effort throughout).
        """
        with contextlib.suppress(Exception):
            self._persist_url_meta()
        self._close_metadata_db_quietly()

    def stop(self):
        if self.single_mode_flag:
            self._stop_after_group = True
            self._stopped_by_request = True
            # 若目前在暫停中，先解除暫停，才能在當前 PID 組完成後停下
            if not self._pause_event.is_set():
                self._pause_event.set()
                self._emit_output("<p><font color='orange'>收到中止要求：已解除暫停，將在當前 PID 組完成後停止</font></p>")
            try:
                if self._active_group_pid is not None:
                    self._q.put(WorkerEvent("output", f"<p><font color='orange'>收到中止要求：目前正在處理 PID {self._active_group_pid}，將在此組完成後停止</font></p>"))
                else:
                    self._q.put(WorkerEvent("output", "<p><font color='orange'>收到中止要求：目前無活動 PID，將立即停止</font></p>"))
                    self._stop_event.set()
                    self._pause_event.set()
            except Exception:
                pass
            return
        self._stopped_by_request = True
        self._pause_event.set()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已停止</font></p>"))
        self._stop_event.set()



