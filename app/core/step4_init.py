"""Step 4 ``__init__`` helper group for ``download_thread`` (file-size refactor).

Verbatim moves of the constructor-time helpers: basic-option assignment,
wait-range/legacy-cooldown resolution, filter-state seeding, per-run mutable
state, the closed-set load, all_url/DB task loading (+ failed-page requeue)
and the step4_init diagnostic emit. Mixed into ``download_thread`` via
``_Step4InitMixin``; every method reaches worker state through inheritance,
so behaviour is unchanged. The ``defer_step4_scan`` branch itself stays in
``thread_download.__init__`` untouched.
"""
from __future__ import annotations

import contextlib
import datetime
import os
import threading
from queue import Queue

from app import i18n
from app.core.pixiv_thread_utils import (
    init_cookie_fields,
    safe_meta_count as _safe_meta_count,
)
from app.core.worker_event import WorkerEvent


class _Step4InitMixin:
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
