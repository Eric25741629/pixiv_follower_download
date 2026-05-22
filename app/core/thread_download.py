import contextlib
import time
import json
import os
import datetime
import re
import glob
import random as pyrandom
import threading
import concurrent.futures
import requests
import io
import zipfile
import shutil
import subprocess
import tempfile
from queue import Queue, Empty
from PIL import Image
from pixiv_api import *
from app.core.worker_event import WorkerEvent
import tag_edit
import pixiv_api
from app.core.pixiv_thread_utils import (
    append_diagnostic_event,
    atomic_write_json,
    atomic_write_text,
    count_text_lines,
    fetch_with_cookie_retry,
    init_cookie_fields,
    load_exist_pid_set,
    mirror_meta_dict_to_db,
    normalize_filter_tags,
    normalize_pid,
    output_err,
    safe_read_json,
    to_int_lenient,
    trash_file,
    write_all_url_file,
)
from app.core.metadata_db import MetadataDB, emit_db_stats, mirror_exist_pid_set, open_metadata_db
from app.core.pixiv_thread_base import (
    PauseableThread,
    _normalize_special_like_rules,
    _resolve_like_threshold,
    _is_ai_artwork_tagged,
    _cookie_usage_label,
    _format_cookie_usage_summary,
)

def _safe_meta_count(db) -> int:
    try:
        return int(db.meta_count())
    except Exception:
        return 0


class download_thread(PauseableThread):
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
        scheduler=None,
        stats_collector=None,
        event_log=None,
        *legacy_args,
        **legacy_kwargs,
    ):
        super().__init__(q, scheduler=scheduler)
        self._event_log = event_log
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
        self._init_jxl_config(
            overrides.get("jxl_enable", jxl_enable),
            overrides.get("jxl_cjxl_path", jxl_cjxl_path),
            overrides.get("jxl_delete_original", jxl_delete_original),
            overrides.get("jxl_effort", jxl_effort),
        )
        self._init_filter_state(
            overrides.get("like_num", 0),
            overrides.get("ban_tag", []),
            overrides.get("must_tag", []),
            overrides.get("special_like_rules", []),
            overrides.get("filename_template", ""),
        )
        self._init_step4_paths_and_state()

        if not os.path.exists(self.download_path):
            os.mkdir(self.download_path)
        # Order matters: ``_load_initial_exist_pid_set`` now reads from
        # ``v_closed_artworks`` in the SQLite cache, so ``_metadata_db``
        # has to be initialised first.
        self.url_meta = self._load_initial_url_meta()
        self._metadata_db = self._init_metadata_db(self.url_meta)
        self.exist_pid = self._load_initial_exist_pid_set()
        self._mirror_exist_pid_to_db()
        self._emit_metadata_db_stats(stage="Step4")
        self._warn_if_meta_empty_with_like_filter()
        self._read_all_url_file_into_state()

        raw_allurl_count = len(self.allurl)
        self.allurl, self._task_filter_stats = self._prepare_download_tasks(self.allurl)
        self.pid_max = len(self.allurl)
        self._emit_step4_init_diag(raw_allurl_count)
        print(self.pid_max)

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
        self._current_account = None  # set by _execute_downloads when scheduler is active
        self.agent = agent
        self.download_time = download_time
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

    def _init_jxl_config(self, jxl_enable, jxl_cjxl_path, jxl_delete_original, jxl_effort):
        """Resolve JXL settings + reset per-run JXL counters."""
        self.jxl_enable = bool(jxl_enable)
        jxl_path_raw = (
            str(jxl_cjxl_path).strip()
            if str(jxl_cjxl_path).strip()
            else r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe"
        )
        self.jxl_cjxl_path = self._resolve_cjxl_path(jxl_path_raw)
        self.jxl_delete_original = bool(jxl_delete_original)
        try:
            self.jxl_effort = int(jxl_effort)
        except Exception:
            self.jxl_effort = 7
        self.jxl_effort = max(1, min(9, self.jxl_effort))
        self._jxl_path_warned = False
        self._jxl_gif_skip_warned = False
        self._jxl_ok_count = 0
        self._jxl_fail_count = 0
        self._jxl_src_total_bytes = 0
        self._jxl_dst_total_bytes = 0
        # Background JXL conversion: a single worker thread drains a FIFO
        # queue so cjxl runs do not block downloads.  All counter mutations
        # stay on this single worker (no lock needed); the main thread only
        # reads them after _drain_jxl_queue() in finalize.
        self._jxl_queue: Queue = Queue()
        self._jxl_worker_thread: threading.Thread | None = None

    def _init_filter_state(self, like_num, ban_tag, must_tag, special_like_rules,
                           filename_template=""):
        """Normalize tag/like filters and seed the per-PID decision cache."""
        self.like_num = like_num if like_num > 0 else 0
        self.ban_tag = ban_tag
        self.must_tag = must_tag
        self.special_like_rules = special_like_rules
        self.filename_template = str(filename_template or "").strip()
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
        try:
            self._q.put(WorkerEvent("output",
                "<p><font color='red'>[警告] all_url_meta.json 為空，"
                "且設定了愛心過濾門檻；步驟 4 將嘗試即時補抓 meta，"
                "若仍失敗會被全部標為「無meta」跳過。建議先重跑步驟 3。</font></p>"
            ))
        except Exception:
            pass

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
        """Read pending URLs into ``self.allurl``; DB-first with file fallback."""
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
        # Fallback: file
        try:
            print("正在讀取 all_url.txt...", self.path + r"/all_url.txt")
            with open(self.path + r"/all_url.txt") as file:
                self.allurl = [line.rstrip() for line in file if line.rstrip()]
            print(f"all_url.txt 讀取完成，URL數量：{len(self.allurl)}")
            print("正在過濾已存在的 PID...")
        except Exception:
            self._q.put(WorkerEvent("finished", 'Task finished'))

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
        try:
            append_diagnostic_event(self.path, event, stage="step4", **fields)
        except Exception:
            pass

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

    def _normalize_artwork_tags(self, tags):
        if isinstance(tags, list):
            source = tags
        elif tags in (None, 404):
            source = []
        else:
            source = [tags]
        out = []
        for t in source:
            s = str(t).strip()
            if s:
                out.append(s.lower())
        return out

    def _tag_hit(self, target_tag, artwork_tags):
        key = str(target_tag).strip().lower()
        if not key:
            return False
        for tag in artwork_tags:
            if key in tag:
                return True
        return False

    def _is_r18g_artwork(self, tag):
        """Gore-adjacent adult content: r-18g / 糞 / 子宮脫."""
        artwork_tags = self._normalize_artwork_tags(tag)
        for marker in ("r-18g", "糞", "子宮脫"):
            if self._tag_hit(marker, artwork_tags):
                return True
        return False

    def _is_r18_artwork(self, tag):
        """General adult content (r-18) excluding r-18g markers."""
        if self._is_r18g_artwork(tag):
            return False
        artwork_tags = self._normalize_artwork_tags(tag)
        return any(t == "r-18" for t in artwork_tags)

    def _is_ai_artwork(self, tag):
        artwork_tags = self._normalize_artwork_tags(tag)
        return _is_ai_artwork_tagged(artwork_tags, self._tag_hit)

    def _resolve_download_target_dir(self, tag, pid, media_kind=None):
        if self.create_dir:
            user_id = pixiv_api.userId('https://www.pixiv.net/artworks/' + str(pid), self.agent)
            base_dir = os.path.join(self.download_path, str(user_id))
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

    def _bump_step4_skip_count(self, reason):
        """Increment a counter; create the key on first sight. Returns the key."""
        key = str(reason or "other")
        try:
            self._step4_filter_skip_counts.setdefault(key, 0)
            self._step4_filter_skip_counts[key] += 1
        except Exception:
            pass
        return key

    def _step4_skip_total(self):
        try:
            return int(sum(int(v or 0) for v in self._step4_filter_skip_counts.values()))
        except Exception:
            return 0

    def _maybe_emit_step4_skip_notice(self):
        if self._step4_filter_skip_notice_emitted:
            return
        self._step4_filter_skip_notice_emitted = True
        self._emit_output("<p><font color='gray'>[Step4過濾] 已啟用精簡輸出，將改為摘要顯示</font></p>")

    def _maybe_emit_step4_skip_summary(self, total):
        try:
            if total > 0 and total % int(self._step4_filter_skip_every) == 0:
                self._q.put(WorkerEvent("output",
                    "<p><font color='gray'>[Step4過濾摘要] 已略過 {} 筆"
                    "（標籤={}、低愛心={}、無meta={}）</font></p>".format(
                        total,
                        int(self._step4_filter_skip_counts.get("tag", 0)),
                        int(self._step4_filter_skip_counts.get("like", 0)),
                        int(self._step4_filter_skip_counts.get("no_meta", 0)),
                    )))
        except Exception:
            pass

    def _record_step4_filter_skip(self, reason, pid_key=None):
        key = self._bump_step4_skip_count(reason)
        try:
            self._diag("step4_filter_skip", reason=key, pid=str(pid_key or ""))
        except Exception:
            pass
        self._maybe_emit_step4_skip_notice()
        self._maybe_emit_step4_skip_summary(self._step4_skip_total())

    def _emit_step4_filter_skip_final_summary(self):
        try:
            total = int(sum(int(v or 0) for v in self._step4_filter_skip_counts.values()))
        except Exception:
            total = 0
        if total <= 0:
            return
        try:
            self._q.put(WorkerEvent("output",
                "<p><font color='gray'>[Step4過濾完成] 共略過 {} 筆（標籤={}、低愛心={}、無meta={}）</font></p>".format(
                    total,
                    int(self._step4_filter_skip_counts.get("tag", 0)),
                    int(self._step4_filter_skip_counts.get("like", 0)),
                    int(self._step4_filter_skip_counts.get("no_meta", 0)),
                )
            ))

        except Exception:
            pass

    def _refresh_cookie_requirement(self, pid, fallback=None):
        pid_key = normalize_pid(pid)
        if not pid_key:
            return fallback
        try:
            if isinstance(getattr(self, '_cookie_requirement_map', None), dict) and pid_key in self._cookie_requirement_map:
                return self._cookie_requirement_map.get(pid_key)
        except Exception:
            pass

        latest = fallback
        # First-time resolution only: if fallback already exists, don't re-query trace.
        if latest is None:
            try:
                latest = pixiv_api.get_pixiv_cookie_requirement(pid_key)
            except Exception:
                latest = fallback
        try:
            if not isinstance(getattr(self, '_cookie_requirement_map', None), dict):
                self._cookie_requirement_map = {}
            self._cookie_requirement_map[pid_key] = latest
        except Exception:
            pass
        return latest

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
                try:
                    db.mark_artwork_revoked(pid)
                except Exception:
                    pass
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
            try:
                print(f"[pixiv_thread] response preview (first): {first_try_resp.text[:500]}")
            except Exception:
                pass
        try:
            stage = "retry" if meta_trace.get("retry_used") else "first"
            print(f"[pixiv_thread] response preview ({stage}): {htmlfile.text[:500]}")
        except Exception:
            pass

    def _fetch_filter_meta_via_scheduler(self, pid_key, url, need_cookie):
        """Network fallback through the AccountScheduler — bound proxy + cooldown applies."""
        acc = self._acquire_account()
        if acc is None:
            return None
        self._record_cookie_usage("step3", pid_key, acc.cookie)
        session = pixiv_api.make_session(acc.proxy_url)

        def _do_fetch():
            if need_cookie is False:
                return pixiv_api.Pixiv_info(url, self.agent, session=session)
            return pixiv_api.Pixiv_info(
                url, self.agent, cookie=acc.cookie, session=session,
            )

        info = None
        try:
            ok, info, _ = self._run_with_network_retry(f"PID {pid_key}", _do_fetch)
        except Exception:
            ok = True
        self._release_account(acc, ok=ok)
        return info

    def _fetch_filter_meta_direct(self, pid_key, url, need_cookie):
        """Network fallback without a scheduler — used by tests / single-account runs."""
        pid_cookie = self._select_cookie_for_pid(pid_key)
        self._record_cookie_usage("step3", pid_key, pid_cookie)
        try:
            if need_cookie is False or not pid_cookie:
                return pixiv_api.Pixiv_info(url, self.agent)
            return pixiv_api.Pixiv_info(url, self.agent, cookie=pid_cookie)
        except Exception:
            return None

    def _build_filter_meta_entry(self, url, tag, like, pagecount, img_url, need_cookie):
        """Compose the meta dict written into self.url_meta after a filter fetch."""
        tag_list = tag if isinstance(tag, list) else []
        like_int = self._to_int(like, like)
        page_int = self._to_int(pagecount, pagecount)
        return {
            "tag": tag_list,
            "like": like_int,
            "pagecount": page_int,
            "img_url": img_url,
            "requires_cookie": need_cookie,
            "artwork_url": url,
            "pixiv_info": {
                "tag": tag_list,
                "like": like_int,
                "pagecount": page_int,
                "img_url": img_url,
                "requires_cookie": need_cookie,
                "queried_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "filter_fetch",
            },
        }

    def _fetch_meta_for_filter(self, pid, allow_network=False):
        pid_key = normalize_pid(pid)
        if not pid_key:
            return None
        meta = self._get_meta(pid_key)
        if meta and (meta.get("tag") is not None or meta.get("like") is not None):
            return meta
        if not allow_network:
            return None
        need_cookie = self._refresh_cookie_requirement(pid_key, fallback=None)
        url = "https://www.pixiv.net/artworks/" + pid_key
        # Route the network fallback through the scheduler when present so
        # the bound proxy + per-account cooldown applies (otherwise this
        # path bypasses proxies and hammers pixiv unthrottled — every PID
        # rate-limits and then the whole step 4 ends up "no_meta").
        if self._scheduler is not None:
            info = self._fetch_filter_meta_via_scheduler(pid_key, url, need_cookie)
        else:
            info = self._fetch_filter_meta_direct(pid_key, url, need_cookie)
        if info == [404]:
            db = getattr(self, "_metadata_db", None)
            if db is not None:
                try:
                    db.mark_artwork_revoked(pid_key)
                except Exception:
                    pass
        normalized = self._normalize_pixiv_info(info)
        if not normalized:
            return None
        tag, like, pagecount, img_url = normalized
        meta = self._build_filter_meta_entry(url, tag, like, pagecount, img_url, need_cookie)
        try:
            self.url_meta[pid_key] = meta
        except Exception:
            pass
        return meta

    def _filter_no_meta_decision(self):
        """Decision when meta is unavailable: skip if a like filter is set, else keep pending."""
        if self.like_num > 0 or self.special_like_rules:
            return False, "no_meta"
        return True, "no_meta"

    def _filter_blocked_by_ban_tag(self, artwork_tags):
        """True iff any ban_tag matches the artwork's tag list."""
        return any(self._tag_hit(blocked, artwork_tags) for blocked in self._ban_tag_norm)

    def _filter_missing_must_tag(self, artwork_tags):
        """True iff at least one must_tag is configured but none match."""
        if not self._must_tag_norm:
            return False
        return not any(self._tag_hit(required, artwork_tags) for required in self._must_tag_norm)

    def _filter_below_like_threshold(self, artwork_tags, like_value):
        """True iff a like threshold is configured AND the artwork's like count is below it."""
        like_limit, _matched_rules = _resolve_like_threshold(
            self.like_num,
            artwork_tags,
            self.special_like_rules,
            self._tag_hit,
            self._to_int,
        )
        return (
            like_limit > 0
            and like_value is not None
            and like_value < like_limit
        )

    def _record_filter_decision(self, pid_key, passed, reason):
        """Cache the per-PID filter decision and (when failing) emit a step4 skip event."""
        if not passed and reason in ("tag", "like", "no_meta"):
            self._record_step4_filter_skip(reason, pid_key=pid_key)
        decision = (passed, reason)
        self._pid_filter_decision[pid_key] = decision
        return decision

    def _passes_pid_filter(self, pid, allow_network=False):
        pid_key = normalize_pid(pid)
        if not pid_key:
            return False, "invalid"
        if pid_key in self._pid_filter_decision:
            cached = self._pid_filter_decision[pid_key]
            if cached[1] != "no_meta" or (not allow_network):
                return cached

        meta = self._fetch_meta_for_filter(pid_key, allow_network=allow_network)
        if not isinstance(meta, dict):
            passed, reason = self._filter_no_meta_decision()
            return self._record_filter_decision(pid_key, passed, reason)

        artwork_tags = self._normalize_artwork_tags(meta.get("tag", []))
        if self._filter_blocked_by_ban_tag(artwork_tags):
            return self._record_filter_decision(pid_key, False, "tag")
        if self._filter_missing_must_tag(artwork_tags):
            return self._record_filter_decision(pid_key, False, "tag")

        like_value = self._to_int(meta.get("like"), None)
        if self._filter_below_like_threshold(artwork_tags, like_value):
            return self._record_filter_decision(pid_key, False, "like")
        return self._record_filter_decision(pid_key, True, "pass")

    _LEGACY_POSITIONAL_SCHEMA = [
        ("jxl_enable", bool),
        ("jxl_cjxl_path", lambda v: str(v).strip() or None),
        ("jxl_delete_original", bool),
        ("jxl_effort", int),
    ]
    _LEGACY_SCALAR_KW_SCHEMA = [
        ("jxl_enable", bool),
        ("jxl_cjxl_path", lambda v: str(v).strip() or None),
        ("jxl_delete_original", bool),
        ("jxl_effort", int),
        ("like_num", lambda v: int(v or 0)),
        ("ai_gen_dir", bool),
        ("filename_template", lambda v: str(v or "").strip() or None),
    ]

    @staticmethod
    def _cast_or_skip(caster, raw):
        """Run ``caster(raw)`` returning the value, or ``None`` on any failure / casted None."""
        try:
            value = caster(raw)
        except Exception:
            return None
        return value

    @staticmethod
    def _apply_legacy_positional(args, overrides):
        """Translate positional legacy args into the overrides dict."""
        if not args:
            return
        for idx, (key, caster) in enumerate(download_thread._LEGACY_POSITIONAL_SCHEMA):
            if idx >= len(args):
                break
            value = download_thread._cast_or_skip(caster, args[idx])
            if value is not None:
                overrides[key] = value

    @staticmethod
    def _apply_legacy_scalar_kwargs(kwargs, overrides):
        """Translate scalar keyword legacy args into the overrides dict."""
        for key, caster in download_thread._LEGACY_SCALAR_KW_SCHEMA:
            if key not in kwargs:
                continue
            value = download_thread._cast_or_skip(caster, kwargs[key])
            if value is not None:
                overrides[key] = value

    @staticmethod
    def _apply_legacy_list_kwargs(kwargs, overrides):
        """Pass-through list kwargs (ban_tag / must_tag) when shaped correctly."""
        for list_key in ("ban_tag", "must_tag"):
            value = kwargs.get(list_key)
            if isinstance(value, list):
                overrides[list_key] = value

    @staticmethod
    def _apply_legacy_special_like_rules(kwargs, overrides):
        """Run special_like_rules through the normalizer when present."""
        if "special_like_rules" not in kwargs:
            return
        try:
            overrides["special_like_rules"] = _normalize_special_like_rules(
                kwargs.get("special_like_rules", [])
            )
        except Exception:
            pass

    @staticmethod
    def _apply_legacy_constructor_args(legacy_args, legacy_kwargs):
        """Resolve backward-compatible positional/keyword args to a dict of overrides.

        Silently skips malformed entries so a caller passing junk cannot break __init__.
        """
        overrides = {}
        kwargs = legacy_kwargs or {}
        download_thread._apply_legacy_positional(legacy_args, overrides)
        download_thread._apply_legacy_scalar_kwargs(kwargs, overrides)
        download_thread._apply_legacy_list_kwargs(kwargs, overrides)
        download_thread._apply_legacy_special_like_rules(kwargs, overrides)
        return overrides

    def _resolve_cjxl_path(self, preferred_path):
        preferred = str(preferred_path or "").strip()
        if preferred and os.path.isfile(preferred):
            return preferred
        candidates = [
            preferred,
            r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe",
            r"C:\Users\Eric\Downloads\jxl-x64-windows-static\bin\cjxl.exe",
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        try:
            user_home = os.path.expanduser("~")
            found = glob.glob(os.path.join(user_home, "Downloads", "jxl*", "bin", "cjxl.exe"))
            if found:
                found = sorted(found, key=lambda x: os.path.getmtime(x), reverse=True)
                return found[0]
        except Exception:
            pass
        return preferred or r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe"

    def _build_jxl_command(self, src_path, dst_path):
        ext = os.path.splitext(str(src_path))[1].lower()
        cmd = [self.jxl_cjxl_path, str(src_path), str(dst_path), "--effort", str(self.jxl_effort)]
        if ext in {".jpg", ".jpeg"}:
            cmd.append("--lossless_jpeg=1")
        else:
            cmd.append("--distance=0")
        return cmd

    def _run_cjxl_once(self, src_path, dst_path):
        cmd = self._build_jxl_command(src_path, dst_path)
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode == 0 and os.path.isfile(dst_path):
            return True, ""
        reason = (completed.stderr or completed.stdout or "").strip()
        if len(reason) > 200:
            reason = reason[:197] + "..."
        if not reason:
            reason = f"cjxl exit={completed.returncode}"
        return False, reason

    def _run_cjxl_with_temp_ascii_path(self, src_path, dst_path, temp_name=None):
        ext = os.path.splitext(str(src_path))[1].lower() or ".bin"
        workdir = tempfile.mkdtemp(prefix="jxl_retry_")
        src_name = str(temp_name or ("input" + ext))
        if "." not in src_name:
            src_name = src_name + ext
        tmp_src = os.path.join(workdir, src_name)
        tmp_dst_name = "1_PID.jxl" if temp_name else "output.jxl"
        tmp_dst = os.path.join(workdir, tmp_dst_name)
        try:
            shutil.copy2(src_path, tmp_src)
            ok, reason = self._run_cjxl_once(tmp_src, tmp_dst)
            if ok and os.path.isfile(tmp_dst):
                shutil.move(tmp_dst, dst_path)
                return True, ""
            return False, reason
        except Exception as err:
            return False, str(err)
        finally:
            try:
                shutil.rmtree(workdir, ignore_errors=True)
            except Exception:
                pass

    _JXL_SUPPORTED_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

    def _warn_cjxl_missing_once(self):
        """Emit the 'cjxl not found' warning at most once per worker run."""
        if self._jxl_path_warned:
            return
        self._jxl_path_warned = True
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='orange'>JXL 已啟用，但找不到 cjxl：{self.jxl_cjxl_path}</font></p>"
            ))
        except Exception:
            pass

    def _handle_existing_jxl_destination(self, src_path):
        """Bump ok counter and optionally delete the original when .jxl already exists."""
        try:
            self._jxl_ok_count += 1
            if self.jxl_delete_original and os.path.isfile(src_path):
                os.remove(src_path)
        except Exception:
            pass

    def _jxl_should_convert(self, src_path):
        """Run all gating checks. Returns (proceed, dst_path, ext).

        proceed=False means the orchestrator should return immediately. Side
        effects: emits the one-shot cjxl-missing warning and handles the
        "destination already exists" accounting + optional original deletion.
        """
        if not self.jxl_enable or not src_path:
            return False, None, None
        src_path = str(src_path)
        ext = os.path.splitext(src_path)[1].lower()
        if ext not in self._JXL_SUPPORTED_EXTS:
            return False, None, None
        if not os.path.isfile(src_path):
            return False, None, None
        if not os.path.isfile(self.jxl_cjxl_path):
            self._warn_cjxl_missing_once()
            return False, None, None
        dst_path = os.path.splitext(src_path)[0] + ".jxl"
        if os.path.isfile(dst_path):
            self._handle_existing_jxl_destination(src_path)
            return False, dst_path, ext
        return True, dst_path, ext

    def _jxl_run_conversion(self, src_path, dst_path, ext):
        """Invoke cjxl with extension-aware retry. Returns (ok, reason)."""
        try:
            if ext == ".gif":
                # GIF uses fixed short ASCII filename directly to avoid path/encoding failures.
                ok, reason = self._run_cjxl_with_temp_ascii_path(
                    src_path, dst_path, temp_name="1_PID.gif"
                )
            else:
                ok, reason = self._run_cjxl_once(src_path, dst_path)
        except Exception as err:
            ok, reason = False, str(err)
        if (not ok) and ext != ".gif":
            # Retry with short ASCII temp path to avoid unicode/long-path decode failures.
            ok, reason2 = self._run_cjxl_with_temp_ascii_path(src_path, dst_path)
            if ok:
                reason = ""
            else:
                reason = reason2 or reason
        # GIF intentionally does not fall back to first-frame static conversion
        # so animation correctness is preserved.
        return ok, reason

    def _jxl_tally_sizes(self, src_path, dst_path):
        """Read src/dst sizes, update running totals, return ``(src_size, dst_size, saved)``.

        Any of the three return values may be ``None`` if a stat call failed.
        Total/stats updates are best-effort so test stubs that don't preset
        the running-total attributes don't crash.
        """
        try:
            src_size = os.path.getsize(src_path)
            dst_size = os.path.getsize(dst_path)
        except Exception:
            return None, None, None
        if src_size < 0 or dst_size < 0:
            return None, None, None
        try:
            self._jxl_src_total_bytes += src_size
            self._jxl_dst_total_bytes += dst_size
        except Exception:
            pass
        try:
            if getattr(self, "_stats_collector", None) is not None:
                self._stats_collector.report_jxl(src_size, dst_size)
        except Exception:
            pass
        return src_size, dst_size, src_size - dst_size

    def _jxl_emit_compare_log(self, src_path, src_size, dst_size, saved):
        """Print the per-file 'src → dst (saved X, Y%)' log line if sizes are known."""
        if src_size is None or saved is None or src_size <= 0:
            return
        try:
            saved_ratio = (saved / float(src_size)) * 100.0
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>JXL 對比：{os.path.basename(src_path)} "
                f"{self._format_size_human(src_size)} → {self._format_size_human(dst_size)}"
                f"（省下 {self._format_size_human(saved)}, {saved_ratio:.2f}%）</font></p>"
            ))
        except Exception:
            pass

    def _jxl_delete_source_if_configured(self, src_path):
        """Delete the source file when jxl_delete_original is True (best-effort)."""
        if not self.jxl_delete_original:
            return
        try:
            os.remove(src_path)
        except Exception:
            pass

    def _jxl_emit_failure_log(self, src_path, reason):
        """Print the per-file failure log line; trims very long reasons."""
        self._jxl_fail_count += 1
        msg = reason if len(reason) <= 120 else reason[:117] + "..."
        if not msg:
            msg = "cjxl conversion failed"
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='orange'>JXL 轉檔失敗：{os.path.basename(src_path)} "
                f"({msg})</font></p>"
            ))
        except Exception:
            pass

    def _jxl_record_outcome(self, src_path, dst_path, ok, reason):
        """Update counters, emit log lines and optionally delete the source."""
        if ok and os.path.isfile(dst_path):
            self._jxl_ok_count += 1
            src_size, dst_size, saved = self._jxl_tally_sizes(src_path, dst_path)
            self._jxl_emit_compare_log(src_path, src_size, dst_size, saved)
            self._jxl_delete_source_if_configured(src_path)
            return
        self._jxl_emit_failure_log(src_path, reason)

    def _convert_file_to_jxl(self, src_path):
        proceed, dst_path, ext = self._jxl_should_convert(src_path)
        if not proceed:
            return
        src_path = str(src_path)
        ok, reason = self._jxl_run_conversion(src_path, dst_path, ext)
        self._jxl_record_outcome(src_path, dst_path, ok, reason)

    def _start_jxl_worker_if_needed(self):
        """Lazily spawn the background cjxl worker on first enqueue."""
        if self._jxl_worker_thread is not None and self._jxl_worker_thread.is_alive():
            return
        t = threading.Thread(target=self._jxl_worker_loop, daemon=True, name="jxl-worker")
        t.start()
        self._jxl_worker_thread = t

    def _jxl_worker_loop(self):
        """Process queued source paths one at a time.  Exits cleanly on the
        ``None`` sentinel pushed by ``_drain_jxl_queue``."""
        while True:
            src_path = self._jxl_queue.get()
            if src_path is None:
                with contextlib.suppress(Exception):
                    self._jxl_queue.task_done()
                return
            try:
                with contextlib.suppress(Exception):
                    self._convert_file_to_jxl(src_path)
            finally:
                with contextlib.suppress(Exception):
                    self._jxl_queue.task_done()

    def _enqueue_jxl(self, src_path):
        """Hand a source file off to the background worker.  Non-blocking;
        falls through silently when JXL is disabled or the path is empty."""
        if not self.jxl_enable or not src_path:
            return
        self._start_jxl_worker_if_needed()
        with contextlib.suppress(Exception):
            self._jxl_queue.put(str(src_path))

    def _discard_pending_jxl_items(self):
        """Drain queued items without running cjxl (used on user stop so the
        user is not left waiting on a long backlog).  Does not interrupt
        the in-flight conversion."""
        while True:
            try:
                self._jxl_queue.get_nowait()
            except (Empty, Exception):
                return
            with contextlib.suppress(Exception):
                self._jxl_queue.task_done()

    def _drain_jxl_queue(self):
        """Wait for queued conversions to finish, then shut the worker down.

        On normal completion, blocks until every enqueued file has been
        processed so the JXL summary is accurate.  On a user-initiated stop,
        discards pending items first (keeps in-flight conversion only) so
        the user is not left waiting minutes for a long backlog."""
        worker = self._jxl_worker_thread
        if worker is None or not worker.is_alive():
            return
        if self._stop_event.is_set():
            self._discard_pending_jxl_items()
        with contextlib.suppress(Exception):
            self._jxl_queue.join()
        with contextlib.suppress(Exception):
            self._jxl_queue.put(None)
            worker.join(timeout=5.0)

    def _normalize_ugoira_frames(self, frame_blobs):
        loaded_frames = []
        max_width = 0
        max_height = 0

        for blob in frame_blobs:
            if not blob:
                continue
            try:
                with Image.open(io.BytesIO(blob)) as img:
                    rgba = img.convert("RGBA")
                frame = rgba.copy()
            except Exception:
                continue
            width, height = frame.size
            if width > max_width:
                max_width = width
            if height > max_height:
                max_height = height
            loaded_frames.append(frame)

        if not loaded_frames:
            raise ValueError("no valid ugoira frames to encode")

        normalized_frames = []
        target_size = (max_width, max_height)
        for frame in loaded_frames:
            if frame.size != target_size:
                canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
                canvas.paste(frame, (0, 0), frame)
                frame = canvas
            normalized_frames.append(frame.convert("P", palette=Image.ADAPTIVE))
        return normalized_frames

    def _save_ugoira_gif(self, frame_blobs, output_path, delay_info):
        frames = self._normalize_ugoira_frames(frame_blobs)
        frame_count = len(frames)

        durations = []
        if isinstance(delay_info, list) and delay_info:
            for value in delay_info[:frame_count]:
                try:
                    durations.append(max(1, int(value)))
                except Exception:
                    durations.append(100)
            if len(durations) < frame_count:
                pad_value = durations[-1] if durations else 100
                durations.extend([pad_value] * (frame_count - len(durations)))
        else:
            durations = [100] * frame_count

        frames[0].save(
            output_path,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            optimize=False,
            disposal=2,
        )

    def _normalize_tag_for_filename(self, raw_tag):
        text = str(raw_tag or "").strip()
        if not text:
            return ""
        # Remove empty bracket pairs after tag cleanup/transliteration.
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r"（\s*）", "", text)
        text = re.sub(r"\[\s*\]", "", text)
        text = re.sub(r"【\s*】", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = text.strip(" _-")
        return text

    def _build_hashtag_text(self, tags, max_len=230):
        if not isinstance(tags, list):
            return " "
        out = []
        current_len = 0
        for many in tags:
            token = self._normalize_tag_for_filename(many)
            if not token:
                continue
            # Keep compatibility with old format: each tag separated by one space.
            extra = len(token) + (1 if out else 0)
            if current_len + extra > int(max_len):
                break
            out.append(token)
            current_len += extra
        if not out:
            # Keep legacy behavior: preserve one leading space even when no tag.
            return " "
        # Keep legacy behavior: two leading spaces before first tag.
        return "  " + " ".join(out)

    @staticmethod
    def _split_timetag(timetag, notime):
        """Return (date, time) parts of a 'YYYYMMDD_HHMMSS' tag, or empty pair when suppressed."""
        if not timetag or notime or "_" not in timetag:
            return "", ""
        date_part, time_part = timetag.split("_", 1)
        return date_part, time_part

    @staticmethod
    def _filename_template_fields(pid, page_suffix, ext, hashtag, timetag, notag, notime):
        """Build the placeholder dict consumed by render_filename_template."""
        date_part, time_part = download_thread._split_timetag(timetag, notime)
        return {
            "pid": str(pid),
            "page": page_suffix or "",
            "ext": ext,
            "hashtag": hashtag if not notag else "",
            "tag": (hashtag.strip() if hashtag and not notag else ""),
            "timetag": timetag if not notime else "",
            "date": date_part,
            "time": time_part,
        }

    @staticmethod
    def _render_template_filename(template, ext, fields):
        """Render the user template, append ext if missing, sanitize the result."""
        from app.core.filename_utils import render_filename_template, sanitize_filename
        rendered = render_filename_template(template, fields)
        if not rendered.lower().endswith("." + str(ext).lower()):
            rendered = rendered + "." + ext
        return sanitize_filename(rendered)

    @staticmethod
    def _render_default_filename(pid, page_suffix, ext, hashtag, timetag, notag, notime):
        """Render the default '[timetag_]PID{pid}{page_suffix}[{hashtag}].{ext}' layout."""
        from app.core.filename_utils import sanitize_filename
        parts = []
        if not notime and timetag:
            parts.append(timetag)
        core = 'PID' + str(pid) + (page_suffix or '')
        if not notag:
            core += hashtag
        parts.append(core)
        return sanitize_filename('_'.join(parts) + '.' + ext)

    @staticmethod
    def _build_download_filename(pid, *, page_suffix, ext, hashtag, timetag, notag, notime,
                                 template=""):
        """Compose a download filename and sanitize it for filesystem safety.

        Default layout: "[timetag_]PID{pid}{page_suffix}[{hashtag}].{ext}".
        When ``template`` is non-empty the user template is rendered instead,
        with placeholders {pid} {page} {ext} {tag} {hashtag} {timetag} {date} {time}.
        notag / notime flags still gate the {hashtag} and {timetag} placeholders
        to preserve user intent.

        The result is always passed through filename_utils.sanitize_filename.
        """
        tmpl = str(template or "").strip()
        if tmpl:
            fields = download_thread._filename_template_fields(
                pid, page_suffix, ext, hashtag, timetag, notag, notime,
            )
            return download_thread._render_template_filename(tmpl, ext, fields)
        return download_thread._render_default_filename(
            pid, page_suffix, ext, hashtag, timetag, notag, notime,
        )

    def _format_size_human(self, value):
        try:
            size = int(value or 0)
        except Exception:
            size = 0
        sign = "-" if size < 0 else ""
        n = abs(size)
        if n < 1000:
            return f"{sign}{n} B"
        units = [
            ("GB", 1000 ** 3),
            ("MB", 1000 ** 2),
            ("KB", 1000),
        ]
        for unit, factor in units:
            if n >= factor:
                return f"{sign}{float(n) / float(factor):.2f} {unit}"
        return f"{sign}{n} B"

    def _emit_countdown_start_log(self, pid, delay, label, color):
        """Print the '[下載等待][label] 等待 N 秒' header at the start of a wait."""
        cookie_used = self._is_cookie_used_for_pid(pid)
        ratio_text = '1.0x' if cookie_used else '0.5x'
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='{color}'>[下載等待][{label}] 等待 {delay} 秒 "
                f"(PID {pid}, 倍率 {ratio_text}, cookie_used={cookie_used})</font></p>"
            ))
        except Exception:
            pass

    def _countdown_tick(self, remaining, respect_group_stop):
        """Run one 1-second tick. Returns True iff the loop should break."""
        if self._stop_event.is_set():
            return True
        if respect_group_stop and self._stop_after_group:
            return True
        while not self._pause_event.is_set():
            if self._stop_event.is_set():
                return True
            self._pause_event.wait(timeout=0.5)
        if self._stop_event.is_set():
            return True
        try:
            self._q.put(WorkerEvent("countdown", remaining))
        except Exception:
            pass
        return self._stop_event.wait(timeout=1.0)

    def _run_download_countdown(self, pid, min_sec, max_sec, *, label, color, respect_group_stop):
        if not self.single_mode_flag:
            return
        delay = self._calc_sleep_delay(min_sec, max_sec, pid=pid)
        self._emit_countdown_start_log(pid, delay, label, color)
        for remaining in range(int(delay), 0, -1):
            if self._countdown_tick(remaining, respect_group_stop):
                break
        try:
            self._q.put(WorkerEvent("countdown", 0))
        except Exception:
            pass

    def _sleep_between_downloads(self, pid):
        # Inter-PID cooldown is owned by AccountScheduler.release() when active.
        if self._scheduler is not None:
            return
        avg = int(getattr(self, "_legacy_pid_cooldown_avg", 35))
        low = max(1, int(avg * 0.7))
        high = max(low, int(avg * 1.3))
        delay = pyrandom.randint(low, high)
        self._run_download_countdown(
            pid,
            delay,
            delay,
            label="PID間",
            color="green",
            respect_group_stop=True,
        )

    def _sleep_within_pid(self, pid):
        # Wait between pages within the same PID.
        self._run_download_countdown(
            pid,
            self.intra_pid_wait_min,
            self.intra_pid_wait_max,
            label="同PID",
            color="gray",
            respect_group_stop=False,
        )

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

    def _calc_sleep_delay(self, min_sec, max_sec, pid=None):
        """Calculate randomized sleep delay between min_sec and max_sec.

        The scheduler-aware path no longer applies cookie-pool speedup; this
        function is now used only for intra-PID polite delays.
        """
        return pyrandom.randint(int(min_sec), int(max_sec))

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

    @staticmethod
    def _new_step4_filter_stats():
        return {
            "input_count": 0,
            "duplicate_count": 0,
            "invalid_count": 0,
            "skipped_exist_count": 0,
            "skipped_like_count": 0,
            "skipped_tag_count": 0,
            "skipped_no_meta_count": 0,
            "output_count": 0,
        }

    def _classify_url_for_filter(self, raw, seen_url):
        """Validate raw URL + extract PID. Returns (pid, normalized_url) or (None, reason)."""
        if not isinstance(raw, str):
            return None, "invalid"
        u = raw.strip()
        if not u:
            return None, "invalid"
        if u in seen_url:
            return None, "duplicate"
        seen_url.add(u)
        pid = normalize_pid(self._extract_pid_from_download_url(u))
        if not pid:
            return None, "invalid"
        return pid, u

    @staticmethod
    def _bump_filter_reason(stats, reason, pid, no_meta_pids):
        """Increment the per-reason counter; tag no_meta PIDs for later requeue."""
        if reason == "like":
            stats["skipped_like_count"] += 1
        elif reason == "tag":
            stats["skipped_tag_count"] += 1
        elif reason == "no_meta":
            stats["skipped_no_meta_count"] += 1
            no_meta_pids.add(pid)
        else:
            stats["invalid_count"] += 1

    def _prepare_download_tasks(self, urls, allow_network=False):
        pending = []
        seen_url = set()
        no_meta_pids = set()  # PIDs that fell through filter for lack of meta
        stats = self._new_step4_filter_stats()
        for raw in urls:
            stats["input_count"] += 1
            pid, payload = self._classify_url_for_filter(raw, seen_url)
            if pid is None:
                if payload == "duplicate":
                    stats["duplicate_count"] += 1
                else:
                    stats["invalid_count"] += 1
                continue
            if pid in self.exist_pid:
                stats["skipped_exist_count"] += 1
                continue
            passed, reason = self._passes_pid_filter(pid, allow_network=allow_network)
            if not passed:
                self._bump_filter_reason(stats, reason, pid, no_meta_pids)
                continue
            pending.append(payload)
        stats["output_count"] = len(pending)
        # On the network-enabled pass, queue PIDs that still failed for
        # lack of meta back into pictures_id.txt so the next step 3 run
        # picks them up. Also log a clear message so the user knows what
        # to do next.
        if allow_network and no_meta_pids:
            self._requeue_no_meta_pids(no_meta_pids)
        self._diag(
            "step4_filter_pass",
            allow_network=bool(allow_network),
            stats=stats,
            no_meta_pid_count=len(no_meta_pids),
        )
        return pending, stats

    @staticmethod
    def _read_pictures_id_set(pending_path):
        """Read pictures_id.txt into a set of stripped non-empty lines (defensive)."""
        if not os.path.isfile(pending_path):
            return set()
        out = set()
        try:
            with open(pending_path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if s:
                        out.add(s)
        except Exception:
            pass
        return out

    def _requeue_no_meta_pids(self, pids: set) -> None:
        """Append PIDs that step 4 couldn't resolve meta for back into
        pictures_id.txt (step 3's pending queue) so the user's next step
        3 run picks them up. Merges with whatever is already there."""
        try:
            pending_path = os.path.join(self.path, "pictures_id.txt")
            existing = self._read_pictures_id_set(pending_path)
            new = {str(p).strip() for p in pids if str(p).strip()}
            added = len(new - existing)
            if added <= 0:
                return
            merged = existing | new
            from app.core.safe_io import atomic_write_text
            ordered = sorted(merged, key=lambda s: int(s) if s.isdigit() else s)
            atomic_write_text(pending_path, ordered, backup=False)
            try:
                self._q.put(WorkerEvent("output",
                    f"<p><font color='orange'>[補meta] {added} 個缺 meta 的 PID "
                    f"已加回 pictures_id.txt（共 {len(merged)} 筆待辦），"
                    f"請再跑一次步驟 3 補抓資料</font></p>"
                ))
            except Exception:
                pass
        except Exception:
            pass
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

    def _download_pid_group(self, pid, urls):
        failed = []
        # Build session with the bound proxy when an account is currently held.
        acc = getattr(self, '_current_account', None)
        if acc is not None:
            from app.core import pixiv_api as _pixiv_api
            sess = _pixiv_api.make_session(acc.proxy_url)
        else:
            sess = requests.Session()
        try:
            for idx, u in enumerate(urls):
                ret = self.gif_or_jpg(u, session=sess)
                if ret == -1:
                    # 已存在或被規則略過，不視為失敗
                    pass
                elif ret is None:
                    failed.append([u, "unknown"])
                elif ret != 0:
                    failed.append(ret)
                elif idx < len(urls) - 1:
                    # 同一 PID 多頁時，頁面間做短暫休眠
                    self._sleep_within_pid(pid)
                if self._stop_event.is_set():
                    break
        finally:
            try:
                sess.close()
            except Exception:
                pass
        return failed

    @staticmethod
    def _parse_pid_from_pid_equals(file):
        # Format: "...PID=12345_p0.jpg" → requires 4 < len < 12.
        try:
            candidate = file.split('PID=')[1].split('_')[0]
        except IndexError:
            return None
        if 4 < len(candidate) < 12:
            return candidate
        return None

    @staticmethod
    def _parse_pid_from_pid_prefix(file):
        # Format: "...PID12345 ..." → requires 4 < len <= 13.
        # Fallback: if too long/short but leading "p"-split is digit, keep dot-stripped form.
        try:
            candidate = file.split('PID')[1].split(' ')[0]
        except IndexError:
            return None
        if 4 < len(candidate) <= 13:
            return candidate
        head = candidate.split('p')[0]
        if head.isdigit():
            return candidate.split('.')[0]
        return None

    @staticmethod
    def _parse_pid_from_underscore(file):
        # Format: "illust_12345_..." → requires 4 < len < 12.
        try:
            candidate = file.split('_')[1]
        except IndexError:
            return None
        if 4 < len(candidate) < 12:
            return candidate
        return None

    def splitID(self, Filelist):
        print(len(Filelist))
        parsers = (
            self._parse_pid_from_pid_equals,
            self._parse_pid_from_pid_prefix,
            self._parse_pid_from_underscore,
        )
        seen = set()
        for file in Filelist:
            if not re.search(r'\.jpg|\.png|\.gif', file):
                continue
            if not re.search(r'PID|illust', file):
                continue
            for parser in parsers:
                pid = parser(file)
                if pid:
                    seen.add(pid)
                    break
        return list(seen)
    
    def get_filelist(self,path):
        file_list = []
        try:
            for root, _, files in os.walk(path):
                for name in files:
                    file_list.append(os.path.join(root, name))
        except Exception:
            pass
        return file_list
    
    def _emit_phase(self, text: str) -> None:
        try:
            self._q.put(WorkerEvent("phase", text))
        except Exception:
            pass

    def _emit_step4_header(self):
        self._q.put(WorkerEvent("output", "<p><font color='red'>下載階段開始...</font></p>"))
        self._q.put(WorkerEvent("output", f"<p><font color='red'>Pending URL: {len(self.allurl)}</font></p>"))
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[Step4過濾設定] like_min={self.like_num}, special_rules={len(self.special_like_rules)}, ban_tag={len(self._ban_tag_norm)}, must_tag={len(self._must_tag_norm)}, ai_dir={bool(self.ai_gen_dir)}</font></p>"
            ))
        except Exception:
            pass
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
            self._q.put(WorkerEvent("finished", 'Task finished'))
            self._q.put(WorkerEvent("next", -1))
        else:
            self._q.put(WorkerEvent("finished", '下載完成'))

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

    def _download_pid_with_scheduler(self, pid, urls):
        """Acquire an account, run download under retry, release. Returns (result, stop)."""
        acc = self._acquire_account()
        if acc is None:
            return [], True  # stop signal or all disabled
        pid_key = normalize_pid(pid) or str(pid)
        self._pid_cookie_selection[pid_key] = acc.cookie  # sticky cookie for this PID
        self._current_account = acc
        ok, result, _ = self._run_with_network_retry(
            f"PID {pid}",
            lambda: self._download_pid_group(pid, urls),
        )
        self._current_account = None
        self._release_account(acc, ok=ok)
        return (result if isinstance(result, list) else []), False

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

    def _execute_downloads_pool(self, pid_order, pid_groups):
        """Multi-thread per-PID-group download via ThreadPoolExecutor (4 workers)."""
        self._q.put(WorkerEvent("output",
            "<p><font color='gray'>下載模式：多執行緒（以 PID 為單位分派；每個 PID 仍共用單一 Session）</font></p>"))
        failed_nested = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as self.executor:
            futures = [
                self.executor.submit(self._download_pid_group, pid, pid_groups.get(pid, []))
                for pid in pid_order
            ]
            total = getattr(self, "_step4_pid_total", len(pid_order))
            done = 0
            for fu in concurrent.futures.as_completed(futures):
                try:
                    failed_nested.append(fu.result())
                except Exception:
                    failed_nested.append([])
                done += 1
                self._step4_pid_done = done
                self._emit_phase(f"步驟 4：下載中（{done} / {total} PID 完成）")
                self._maybe_flush_url_meta_periodically(done)
        return failed_nested

    def _execute_downloads(self, pid_order, pid_groups):
        if self.single_mode_flag:
            return self._execute_downloads_single(pid_order, pid_groups)
        return self._execute_downloads_pool(pid_order, pid_groups)

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
            with self._attempted_urls_lock:
                attempted_snapshot = set(self._attempted_urls)
        except Exception:
            attempted_snapshot = set()
        unattempted_urls = [u for u in self.allurl if u not in attempted_snapshot]
        remaining_urls = []
        seen = set()
        for u in stop_to_download + failed_to_download + unattempted_urls:
            if u in seen:
                continue
            seen.add(u)
            remaining_urls.append(u)
        self._diag(
            "step4_remaining_computed",
            stop_queue_count=len(stop_to_download),
            failed_url_count=len(failed_to_download),
            unattempted_count=len(unattempted_urls),
            remaining_count=len(remaining_urls),
            attempted_count=len(attempted_snapshot),
        )
        return remaining_urls

    def _init_metadata_db(self, json_meta):
        """Open the SQLite metadata cache and migrate JSON contents on first use."""
        return open_metadata_db(self.path, json_meta, event_log=getattr(self, "_event_log", None))

    def _emit_metadata_db_stats(self, stage="Step"):
        """Print a one-liner with current SQLite cache size."""
        emit_db_stats(getattr(self, "_metadata_db", None), self._q, stage=stage)

    def _mirror_exist_pid_to_db(self):
        """Best-effort copy of the in-memory exist_pid set into the SQLite cache."""
        mirror_exist_pid_set(getattr(self, "_metadata_db", None), self.exist_pid)

    def _sync_meta_to_db(self):
        """Mirror the in-memory ``self.url_meta`` into the SQLite cache."""
        mirror_meta_dict_to_db(getattr(self, "_metadata_db", None), self.url_meta)

    @staticmethod
    def _meta_to_db_kwargs(meta):
        """Translate a self.url_meta entry to MetadataDB.upsert_meta kwargs.

        Tolerates non-dict input (returns all-None fields) and accepts both
        ``tag``/``tags`` for tag list aliases plus ``pagecount``/``page_count``
        and ``updated_at``/``checked_at`` for timestamp aliases.
        """
        if not isinstance(meta, dict):
            return {
                "tags": None, "like_count": None, "page_count": None,
                "img_url": None, "requires_cookie": None, "updated_at": None,
            }
        tags = meta.get("tag")
        if tags is None:
            tags = meta.get("tags")
        return {
            "tags": list(tags) if isinstance(tags, list) else None,
            "like_count": meta.get("like"),
            "page_count": meta.get("pagecount") or meta.get("page_count"),
            "img_url": meta.get("img_url"),
            "requires_cookie": meta.get("requires_cookie"),
            "updated_at": meta.get("updated_at") or meta.get("checked_at"),
        }

    def _upsert_meta_in_db(self, pid_key, meta):
        """Best-effort per-PID upsert into SQLite (no-op if DB is unavailable)."""
        db = getattr(self, "_metadata_db", None)
        if db is None or not pid_key:
            return
        try:
            db.upsert_meta(pid_key, **self._meta_to_db_kwargs(meta))
        except Exception:
            pass

    def _persist_url_meta(self):
        """Sync self.url_meta to SQLite (DB is now the primary store)."""
        lock = getattr(self, "_url_meta_lock", None)
        if lock is not None:
            lock.acquire()
        try:
            self._sync_meta_to_db()
        finally:
            if lock is not None:
                lock.release()

    def _mark_completed_urls_in_db(self, fail_records):
        """Mark URLs that were attempted and didn't fail as done in SQLite.
        Silently skips if the DB is not wired or anything goes wrong — this
        is purely an optimisation for subsequent runs."""
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return
        try:
            fail_url_set = {str(u) for u, _ in fail_records}
            with self._attempted_urls_lock:
                attempted = set(self._attempted_urls)
            done_urls = [u for u in self.allurl if u in attempted and u not in fail_url_set]
            db.mark_urls_done(done_urls)
        except Exception:
            pass

    def _finalize_downloads(self, failed_nested):
        fail_records = self._classify_download_results(failed_nested)
        if fail_records:
            err_lines = [f"{url_text} {info_text}" for url_text, info_text in fail_records]
            atomic_write_text(self.path + "/err_url.txt", err_lines, backup=False)
            self._shadow_mark_failures(fail_records)
        self._diag("step4_fail_records", fail_record_count=len(fail_records))
        stop_to_download = [self.q.get() for _ in range(self.q.qsize())]
        remaining_urls = self._compute_remaining_urls(stop_to_download, fail_records)
        self._mark_completed_urls_in_db(fail_records)
        self._write_all_url_file(remaining_urls, reason="step4_remaining")
        self._emit_output(f"<p><font color='gray'>已更新 all_url.txt，剩餘 {len(remaining_urls)} 筆待下載</font></p>")
        self._persist_url_meta()
        return remaining_urls

    def _shadow_mark_failures(self, fail_records) -> None:
        """Mirror err_url.txt writes into ``pages(status='failed')``.

        ``fail_records`` is the list of ``[url, info]`` produced by
        ``_classify_download_results``; we parse each URL into (pid, page)
        and call ``mark_page_failed`` so Phase 6's auto-retry path can
        pick them up.  Silent on any failure — shadow write is best-effort.
        """
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return
        try:
            from app.core.pid_filesystem import parse_pid_and_page_from_url
            for url_text, info_text in fail_records:
                pid, pidx = parse_pid_and_page_from_url(str(url_text))
                if pid is None or pidx is None:
                    continue
                db.mark_page_failed(
                    pid, pidx,
                    failure_reason=str(info_text),
                    url=str(url_text),
                )
        except Exception:
            pass

    def _maybe_flush_exist_pid(self, pid: str) -> None:
        """Add pid to exist_pid and mark downloaded in DB immediately."""
        pid_key = normalize_pid(pid) or str(pid)
        if pid_key:
            self.exist_pid.add(pid_key)
            db = getattr(self, "_metadata_db", None)
            if db is not None:
                try:
                    db.mark_downloaded(pid_key)
                except Exception:
                    pass

    # 每 N 個 PID 組之後額外 flush url_meta；exist_pid + DB 已逐筆寫，
    # 這裡只是把每組 cookie_used / requires_cookie 等 url_meta 變動推到 DB。
    _STEP4_URL_META_FLUSH_EVERY = 10

    def _maybe_flush_url_meta_periodically(self, done_count: int) -> None:
        """Best-effort periodic url_meta DB sync. Idempotent under concurrent calls."""
        try:
            every = int(self._STEP4_URL_META_FLUSH_EVERY)
        except Exception:
            every = 10
        if every <= 0 or done_count <= 0:
            return
        if done_count % every != 0:
            return
        try:
            self._persist_url_meta()
        except Exception:
            pass

    def _refresh_and_write_exist_pid(self):
        # Merge disk state into memory (full scan catches PIDs from previous runs)
        disk_pids = load_exist_pid_set(self.path)
        self.exist_pid.update(disk_pids)
        download_id = self.splitID(self.get_filelist(self.download_path))
        self.exist_pid.update(download_id)
        # SQLite is now the primary store — bulk sync the final set
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            try:
                db.import_downloaded_set(self.exist_pid)
            except Exception:
                pass
        # Trash any remaining legacy files
        for old in [self.legacy_exist_json_path, self.exist_txt_path]:
            if os.path.isfile(old):
                trash_file(old, self.path)

    def _emit_step4_summary_and_finalize(self, remaining_urls):
        if self.jxl_enable:
            # Wait for any backlog the background worker still has — counters
            # used in the summary below are written only by that worker, so
            # we must drain before reading.  On user-stop, drain discards
            # pending items so the summary fires promptly.
            if self._jxl_worker_thread is not None and self._jxl_worker_thread.is_alive():
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
        self._q.put(WorkerEvent("timechanged", datetime.datetime.strftime(self.download_time, '%Y-%m-%d %H:%M:%S')))
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
            self._q.put(WorkerEvent("finished", 'Task finished'))
            self._q.put(WorkerEvent("next", -1))
        else:
            self._diag(
                "step4_finished",
                status="completed",
                remaining_count=len(remaining_urls),
                exist_pid_count=len(self.exist_pid),
            )
            self._q.put(WorkerEvent("finished", '下載完成'))

    def run(self):
        try:
            if self._stats_collector is not None:
                self._stats_collector.reset_session()
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
                f"<p><font color='gray'>[Step4] 開始 PID 分組...</font></p>"))
            pid_order, pid_groups = self._group_urls_by_pid(self.allurl)
            self._diag("step4_grouped", pid_count=len(pid_order), url_count=len(self.allurl))
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[Step4] PID 分組完成：{len(pid_order)} 個 PID、{len(self.allurl)} 個 URL</font></p>"))
            self._emit_phase(f"步驟 4：下載中（0 / {len(pid_order)} PID 完成）")
            self._step4_pid_total = len(pid_order)
            self._step4_pid_done = 0
            failed_nested = self._execute_downloads(pid_order, pid_groups)
            self._emit_phase("步驟 4：整理失敗項目...")
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[Step4] 下載迴圈結束，整理失敗項目中...</font></p>"))
            remaining_urls = self._finalize_downloads(failed_nested)
            self._emit_phase("步驟 4：更新已下載紀錄...")
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[Step4] 寫入已下載 PID 紀錄...</font></p>"))
            self._refresh_and_write_exist_pid()
            self._emit_phase("步驟 4：產生摘要...")
            self._emit_step4_summary_and_finalize(remaining_urls)
            self._emit_phase("")
        except Exception as e:
            self._emit_phase("")
            self._diag("step4_exception", error=output_err(e))
            self._q.put(WorkerEvent("output", 'Task failed'))
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
            if page is None:
                self._q.put(WorkerEvent("output",
                    f"<p><font color='black'>[下載] PID {pid_for_log} ({media})</font></p>"))
            else:
                self._q.put(WorkerEvent("output",
                    f"<p><font color='black'>[下載] PID {pid_for_log} 第 {int(page) + 1} 張 ({media})</font></p>"))
        except Exception:
            pass

    def _record_attempted_and_advance(self, original_url):
        """Mark a URL as attempted and bump the progress counter."""
        try:
            with self._attempted_urls_lock:
                self._attempted_urls.add(original_url)
        except Exception:
            pass
        if not self._stop_event.is_set():
            self.pid_now = self.pid_now + 1
            self._q.put(WorkerEvent("progress", (1, self.pid_max)))

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
            print('頝喲?')
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
            self.q.put(original_url)
            return 0
        is_ugoira = 'ugoira' in resolved_url
        return self._dispatch_download(original_url, resolved_url, session, is_ugoira)
    def _stamp_step4_gif_cookie_usage(self, pid_key, source):
        """Mark requires_cookie + cookie_used on the in-memory url_meta entry and DB."""
        try:
            meta = dict(self._get_meta(pid_key))
            meta["requires_cookie"] = True
            meta["cookie_used"] = True
            meta["cookie_used_source"] = str(source)
            meta["cookie_used_updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.url_meta[pid_key] = meta
            self._upsert_meta_in_db(pid_key, meta)
        except Exception:
            pass

    def _atomic_write_url_meta_with_raw_fallback(self):
        """atomic_write_json with raw open()-fallback on failure."""
        try:
            atomic_write_json(self.url_meta_path, self.url_meta, backup=True)
        except Exception:
            try:
                with open(self.url_meta_path, 'w', encoding='utf-8') as f:
                    json.dump(self.url_meta, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    def _mark_gif_cookie_usage(self, pid, used, source="unknown"):
        pid_key = normalize_pid(pid) or str(pid)
        used_flag = bool(used)
        try:
            self._pid_cookie_used[pid_key] = used_flag
        except Exception:
            pass
        if not used_flag:
            return

        # Hold _url_meta_lock so the read-modify-write of self.url_meta and
        # the JSON serialization-then-replace cannot race a concurrent worker.
        lock = getattr(self, "_url_meta_lock", None)
        if lock is not None:
            lock.acquire()
        try:
            self._stamp_step4_gif_cookie_usage(pid_key, source)
            self._atomic_write_url_meta_with_raw_fallback()
        finally:
            if lock is not None:
                lock.release()

        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='blue'>[GIF][Cookie] PID {pid_key} 使用 cookies（來源：{source}），已更新 all_url_meta 暫存</font></p>"
            ))
        except Exception:
            pass

    def _stream_ugoira_zip_bytes(self, url, headers, http, pid_cookie):
        """Fetch a ugoira zip URL and return the full bytes blob (or None on error).

        Advances ``self.download_time`` (under timelock) and reports stats
        when streaming succeeds, matching the original inline behavior.
        """
        try:
            resp = http.get(url, headers=headers, stream=True)
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError):
            raise
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        with self.timelock:
            self.download_time = self.download_time + datetime.timedelta(seconds=1)
            print(datetime.datetime.strftime(self.download_time, '%Y-%m-%d %H:%M:%S'))
        chunks = [data for data in resp.iter_content(chunk_size=65536) if data]
        zip_bytes = b"".join(chunks)
        if self._stats_collector is not None and zip_bytes:
            self._stats_collector.report_bytes(len(zip_bytes))
            label = _cookie_usage_label(pid_cookie, self.cookie_pool, self._cookie_alias_map)
            self._stats_collector.report_request(label)
        return zip_bytes or None

    def _extract_ugoira_frame_blobs(self, zip_bytes):
        """Return the per-frame byte blobs from a ugoira zip, in archive order."""
        frame_blobs = []
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zipo:
            for member in zipo.namelist():
                if member.endswith('/'):
                    continue
                frame_blobs.append(zipo.read(member))
        return frame_blobs

    def _build_ugoira_save_path(self, pid, tag, my_time):
        """Build the absolute save path for a ugoira GIF, with fallback on naming failure."""
        try:
            hashtag = self._build_hashtag_text(tag, max_len=230)
            name = self._build_download_filename(
                pid,
                page_suffix="",
                ext="gif",
                hashtag=hashtag,
                timetag=my_time.strftime('%Y%m%d_%H%M%S'),
                notag=self.notag,
                notime=self.notime,
                template=getattr(self, "filename_template", ""),
            )
        except Exception:
            name = 'illust_' + pid + my_time.strftime('_%Y%m%d_%H%M%S.gif')
        target_dir = self._resolve_download_target_dir(tag, pid, media_kind='GIF')
        return os.path.join(target_dir, name)

    def _diag_ugoira_meta_fetch(self, pid, meta_trace):
        """Append a step4 diagnostic record describing the ugoira meta fetch result."""
        try:
            self._diag(
                "ugoira_meta_fetch",
                pid=str(pid),
                first_try_status=meta_trace.get("first_try_status"),
                retry_used=bool(meta_trace.get("retry_used")),
                retry_with_cookie_status=meta_trace.get("retry_with_cookie_status"),
                final_status=meta_trace.get("final_status"),
            )
        except Exception:
            pass

    def _maybe_mark_meta_retry_cookie(self, pid, meta_trace):
        """If the with-cookie retry succeeded, record cookie usage and return True."""
        try:
            retry_used = bool(meta_trace.get("retry_used"))
            retry_status = int(meta_trace.get("retry_with_cookie_status") or 0)
        except Exception:
            return False
        if retry_used and retry_status == 200:
            self._mark_gif_cookie_usage(pid, True, source="ugoira_meta_retry")
            return True
        return False

    @staticmethod
    def _parse_ugoira_meta_payload(htmlfile, pid):
        """Parse the ugoira_meta JSON. Returns (download_url, delay_info) or None on bad payload."""
        try:
            gif_info = json.loads(htmlfile.content)['body']
            download_url = gif_info['originalSrc']
            delay_info = [item["delay"] for item in gif_info["frames"]]
            return download_url, delay_info
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[pixiv_thread] PID {pid} JSON parse failed: {e}")
            print(f"[pixiv_thread] response preview: {htmlfile.text[:500]}")
            return None

    def _fetch_ugoira_meta(self, pid, pid_cookie, need_cookie, session):
        """Fetch ugoira_meta with cookie-retry. Returns (download_url, delay_info, need_cookie)
        on success, or ``None`` on any failure (404 / parse error / non-200)."""
        url = 'https://www.pixiv.net/ajax/illust/%s/ugoira_meta?lang=zh_tw' % pid
        headers = self._build_artwork_headers(pid, pid_cookie, need_cookie)
        self._mark_gif_cookie_usage(
            pid,
            bool(need_cookie is True and pid_cookie),
            source="ugoira_meta_initial",
        )
        http = session if session is not None else requests
        htmlfile, meta_trace, first_try_resp = fetch_with_cookie_retry(
            http_get=http.get,
            url=url,
            headers=headers,
            cookies=pid_cookie,
            retry_statuses=(403, 404),
        )
        if self._maybe_mark_meta_retry_cookie(pid, meta_trace):
            need_cookie = True
        self._diag_ugoira_meta_fetch(pid, meta_trace)
        if htmlfile.status_code != 200:
            self._log_ugoira_meta_failure(pid, htmlfile, meta_trace, first_try_resp)
            return None
        htmlfile.raise_for_status()
        if self._stats_collector is not None:
            label = _cookie_usage_label(pid_cookie, self.cookie_pool, self._cookie_alias_map)
            self._stats_collector.report_request(label)
        parsed = self._parse_ugoira_meta_payload(htmlfile, pid)
        if parsed is None:
            return None
        download_url, delay_info = parsed
        return download_url, delay_info, need_cookie

    def gif_download(self, url, session=None):
        my_time = self.download_time
        try:
            pid, pid_cookie, need_cookie = self._resolve_pid_and_cookie(url, source="step4")
            normalized = self._load_artwork_metadata(pid, pid_cookie)
            if not normalized:
                try:
                    self._q.put(WorkerEvent("output",
                        f"<p><font color='orange'>PID {pid} 取得 ugoira 資訊失敗，"
                        f"已標記為失敗任務</font></p>"))
                except Exception:
                    pass
                return [url, my_time.strftime('%Y%m%d_%H%M%S')]
            tag, like, pagecount, img_url = normalized
            meta = self._fetch_ugoira_meta(pid, pid_cookie, need_cookie, session)
            if meta is None:
                return None
            download_url, delay_info, need_cookie = meta
            url = download_url
            http = session if session is not None else requests
            headers = self._build_artwork_headers(pid, pid_cookie, need_cookie, honour_pid_used=True)
            my_time = self.download_time
            zip_bytes = self._stream_ugoira_zip_bytes(url, headers, http, pid_cookie)
            if not zip_bytes:
                return [url, my_time.strftime('%Y%m%d_%H%M%S')]
            frame_blobs = self._extract_ugoira_frame_blobs(zip_bytes)
            if not frame_blobs:
                return [url, my_time.strftime('%Y%m%d_%H%M%S')]
            saved_gif_path = self._build_ugoira_save_path(pid, tag, my_time)
            self._save_ugoira_gif(frame_blobs, saved_gif_path, delay_info)
            if self._stats_collector is not None:
                self._stats_collector.report_file(True)
            self._enqueue_jxl(saved_gif_path)
            return 0
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError):
            # Network/proxy failures must propagate so the scheduler-aware caller
            # can disable the cookie/proxy for this run.
            raise
        except Exception as err:
            print(err, self.cookies)
        return [url, my_time.strftime('%Y%m%d_%H%M%S')]

    _JPG_USER_AGENT = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/99.0.4844.82 Safari/537.36'
    )

    def _jpg_advance_timetag(self):
        """Reserve and return a unique timetag for this download (timelock-guarded)."""
        with self.timelock:
            timetag = self.download_time.strftime('%Y%m%d_%H%M%S')
            self.download_time += datetime.timedelta(seconds=1)
        return timetag

    @staticmethod
    def _jpg_extract_page_and_format(url):
        """Pull the page number and file extension out of a pximg URL."""
        url_str = str(url)
        page = url_str.rsplit('_', 1)[1].rsplit('.', 1)[0]
        picture_format = url_str.rsplit('.', 1)[1]
        return page, picture_format

    def _jpg_build_headers(self, pid, pid_cookie, need_cookie):
        """Headers for the jpg fetch — same shape as gif but with a different UA."""
        headers = self._build_artwork_headers(pid, pid_cookie, need_cookie)
        headers['User-Agent'] = self._JPG_USER_AGENT
        return headers

    def _jpg_resolve_filename(self, pid, page_suffix, ext, tag, timetag):
        """Build the final filename, falling back to a safe default on any error."""
        try:
            hashtag = self._build_hashtag_text(tag, max_len=230)
            return self._build_download_filename(
                pid,
                page_suffix=page_suffix,
                ext=ext,
                hashtag=hashtag,
                timetag=timetag,
                notag=self.notag,
                notime=self.notime,
                template=getattr(self, "filename_template", ""),
            )
        except Exception:
            return 'illust_' + pid + page_suffix + timetag + '.' + ext

    def _jpg_stream_to_disk(self, htmlfile, filepath):
        """Stream the HTTP body to disk in 1 KiB chunks; returns total bytes written."""
        size = 0
        chunk_size = 1024
        with open(filepath, 'wb') as file:
            for data in htmlfile.iter_content(chunk_size=chunk_size):
                file.write(data)
                size += len(data)
        return size

    def _jpg_attempt(self, url, session, timetag):
        """One download attempt. Returns 0 on success, None on a recoverable error."""
        pid, pid_cookie, need_cookie = self._resolve_pid_and_cookie(url, source="step4")
        normalized = self._load_artwork_metadata(pid, pid_cookie)
        if not normalized:
            raise ValueError("Pixiv_info 回傳格式異常")
        tag, like, pagecount, img_url = normalized
        if like == 404 and tag == 404:
            db = getattr(self, "_metadata_db", None)
            if db is not None:
                try:
                    db.mark_artwork_revoked(pid)
                except Exception:
                    pass
            return 0  # treat as success — nothing to download
        page, picture_format = self._jpg_extract_page_and_format(url)
        headers = self._jpg_build_headers(pid, pid_cookie, need_cookie)
        try:
            self._pid_cookie_used[str(pid)] = bool(need_cookie is True and pid_cookie)
        except Exception:
            pass
        http = session if session is not None else requests
        htmlfile = http.get(url, headers=headers, stream=True, timeout=5)
        htmlfile.raise_for_status()
        if self._stats_collector is not None:
            label = _cookie_usage_label(pid_cookie, self.cookie_pool, self._cookie_alias_map)
            self._stats_collector.report_request(label)
        if htmlfile.status_code != 200:
            return 0
        name = self._jpg_resolve_filename(pid, page, picture_format, tag, timetag)
        target_dir = self._resolve_download_target_dir(str(tag), pid)
        filepath = os.path.join(target_dir, name)
        size = self._jpg_stream_to_disk(htmlfile, filepath)
        if self._stats_collector is not None:
            self._stats_collector.report_bytes(size)
            self._stats_collector.report_file(True)
        self._enqueue_jxl(filepath)
        return 0

    def jpg_download(self, url, session=None):
        timetag = self._jpg_advance_timetag()
        last_err = None
        for i in range(0, 5):  # 最多重試 5 次，失敗就回傳錯誤
            try:
                return self._jpg_attempt(url, session, timetag)
            except (requests.exceptions.ProxyError,
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError):
                # Bypass the retry loop entirely — the proxy is dead, retrying
                # against the same proxy will not help. Let the scheduler disable
                # this cookie via release(ok=False).
                raise
            except Exception as err:
                last_err = err
                if i < 4:
                    time.sleep(min(30.0, (2 ** i) + pyrandom.random()))
                    continue
        print(last_err)
        if self._stats_collector is not None:
            self._stats_collector.report_file(False)
        return [url, timetag]

    def flush_for_shutdown(self):
        """Synchronously persist in-flight metadata and close the SQLite cache.

        Called by the window-close hook in flet_app.py just before
        ``os._exit(0)``. Bypasses the worker's async stop machinery so the
        flush completes even when the process is about to be killed; safe to
        call multiple times (best-effort throughout).
        """
        try:
            self._persist_url_meta()
        except Exception:
            pass
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

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



