import contextlib
import os
import datetime
import re
import random as pyrandom
import threading
import requests
from queue import Queue
from pixiv_api import *
from app.core.worker_event import WorkerEvent
import pixiv_api
from app.core.pixiv_thread_utils import (
    append_diagnostic_event,
    cookie_usage_label,
    count_text_lines,
    init_cookie_fields,
    normalize_pid,
    normalize_pid_set,
    output_err,
    read_pid_lines,
    safe_read_json,
    to_int_lenient,
)
from app.core.pixiv_thread_base import (
    PauseableThread,
    _normalize_special_like_rules,
)
from app.core.step3_filters import _Step3FiltersMixin
from app.core.step3_meta_migration import _Step3MigrationMixin
from app.core.step3_persistence import _Step3PersistenceMixin

def _safe_meta_count(db) -> int:
    try:
        return int(db.meta_count())
    except Exception:
        return 0


class get_img_url_thread(PauseableThread, _Step3FiltersMixin,
                         _Step3MigrationMixin, _Step3PersistenceMixin):
    pid_max=0
    pid_now=0
    path=os.getenv('APPDATA')+r'/pixiv_download/'
    def __init__(
        self,
        q,
        Author_list,
        Agent,
        cookies,
        exist_pid,
        ban_tag,
        must_tag,
        like_num,
        no_to_check,
        base_path=None,
        single_thread_mode=False,
        pid_wait_nocookie_min=1,
        pid_wait_nocookie_max=6,
        special_like_rules=None,
        scheduler=None,
        stats_collector=None,
        *,
        event_log=None,
        rescrape_within_days=0,
        live=None,
        r18_like_num=0,
    ):
        super().__init__(q, scheduler=scheduler)
        self._stats_collector = stats_collector
        self._event_log = event_log
        # Live-apply: re-pull in-scope settings at each PID boundary when the
        # user saves mid-run. None -> behaves exactly as before (snapshot only).
        self._live = live
        self._live_sig = None
        self._rescrape_within_days = self._coerce_rescrape_days(rescrape_within_days)
        self._init_basic_options(
            Author_list, Agent, cookies, exist_pid, ban_tag, must_tag,
            like_num, no_to_check, base_path, single_thread_mode, special_like_rules,
            r18_like_num=r18_like_num,
        )
        self.pid_wait_nocookie_min, self.pid_wait_nocookie_max = (
            self._resolve_nocookie_wait_range(pid_wait_nocookie_min, pid_wait_nocookie_max)
        )
        self._init_step3_state()
        self._safe_emit_init("<p><font color='gray'>[Step3初始化] 載入 URL 中繼資料快取...</font></p>")
        self.url_meta = self._load_initial_url_meta()
        self._metadata_db = self._init_metadata_db(self.url_meta)
        self._emit_metadata_db_stats(stage="Step3")
        self._safe_emit_init(
            f"<p><font color='gray'>[Step3初始化] metadata DB 載入，"
            f"開始 schema 遷移檢查...</font></p>"
        )
        self._migrate_url_meta_schema()
        self._revoked_pid_set = set(read_pid_lines(self.revoked_pid_path))
        self._safe_emit_init(
            f"<p><font color='gray'>[Step3初始化] 失效 PID 名單 "
            f"{len(self._revoked_pid_set)} 筆</font></p>"
        )
        self._load_cookie_requirement_cache()
        self._safe_emit_init(
            f"<p><font color='gray'>[Step3初始化] Cookie 需求快取 "
            f"{len(self._cookie_requirement_map)} 筆，初始化完成</font></p>"
        )
        self._emit_step3_init_diag()

    # ── __init__ helpers ───────────────────────────────────────────────────

    def _safe_emit_init(self, html):
        try:
            self._q.put(WorkerEvent("output", html))
        except Exception:
            pass

    def _init_basic_options(
        self, Author_list, Agent, cookies, exist_pid, ban_tag, must_tag,
        like_num, no_to_check, base_path, single_thread_mode, special_like_rules,
        r18_like_num=0,
    ):
        """Plain attribute assignments + cookie unpacking."""
        self.Author_list = Author_list
        self.Agent = Agent
        (self.cookie_entries, self.cookie_pool,
         self._cookie_alias_map, self.cookies) = init_cookie_fields(cookies)
        self._pid_cookie_selection = {}
        self._pid_cookie_alias_selection = {}
        self.exist_pid = normalize_pid_set(exist_pid)
        self.ban_tag = ban_tag
        self.must_tag = must_tag
        self.like_num = like_num
        self.r18_like_num = r18_like_num if r18_like_num and r18_like_num > 0 else 0
        self.special_like_rules = _normalize_special_like_rules(special_like_rules)
        self.no_to_check = no_to_check
        self._ban_tag_norm = self._normalize_filter_tags(self.ban_tag)
        self._must_tag_norm = self._normalize_filter_tags(self.must_tag)
        if isinstance(base_path, str) and base_path.strip():
            self.path = base_path
        self.single_mode_flag = bool(single_thread_mode)

    @staticmethod
    def _resolve_nocookie_wait_range(raw_min, raw_max):
        """Coerce + clamp the per-PID no-cookie wait window."""
        try:
            lo, hi = int(raw_min), int(raw_max)
        except Exception:
            lo, hi = 1, 6
        if lo < 0:
            lo = 0
        if hi < lo:
            hi = lo
        return lo, hi

    def _apply_live_settings_if_changed(self):
        """Re-pull in-scope filters/waits/rescrape when settings.json changed.

        Called at each PID boundary in :meth:`_run_processing_loop`. Step 3
        reads PIDs from ``pictures_id.txt`` (not like-prefiltered), so a changed
        like/tag filter applies in both directions on the NEXT PID. Recomputes
        derived tag-normal forms and clears the per-PID decision cache.
        """
        live = getattr(self, "_live", None)
        if live is None:
            return
        sig = live.signature()
        if sig == getattr(self, "_live_sig", None):
            return
        s = live.sections()
        self._live_sig = sig
        dl = s.get("download", {}) or {}
        perf = s.get("performance", {}) or {}
        try:
            like = int(dl.get("like_num", 0) or 0)
        except (TypeError, ValueError):
            like = 0
        self.like_num = like if like > 0 else 0
        self.ban_tag = list(dl.get("ban_tag", []) or [])
        self.must_tag = list(dl.get("must_tag", []) or [])
        self.special_like_rules = _normalize_special_like_rules(
            dl.get("special_like_rules", []) or []
        )
        self._ban_tag_norm = self._normalize_filter_tags(self.ban_tag)
        self._must_tag_norm = self._normalize_filter_tags(self.must_tag)
        self.pid_wait_nocookie_min, self.pid_wait_nocookie_max = (
            self._resolve_nocookie_wait_range(
                perf.get("pid_wait_nocookie_min", 1), perf.get("pid_wait_nocookie_max", 6)
            )
        )
        self._rescrape_within_days = self._coerce_rescrape_days(
            dl.get("rescrape_within_days", 0)
        )
        try:
            self._pid_filter_decision.clear()
        except Exception:
            self._pid_filter_decision = {}

    def _init_step3_state(self):
        """Initialize per-run mutable Step 3 state (queues, counters, paths)."""
        self.tag_queue = Queue()
        self.like_queue = Queue()
        self._step3_filter_skip_counts = {"ban_tag": 0, "must_tag": 0, "like": 0}
        self._step3_filter_skip_notice_emitted = False
        self._step3_filter_skip_every = 200
        self._step3_query_counts = {"network": 0, "cache": 0, "skip": 0}
        self._step3_cookie_req_counts = {"need": 0, "free": 0, "unknown": 0}
        self._step3_wait_applied_count = 0
        self._step3_query_notice_every = 200
        self.url_meta = {}
        # PIDs whose url_meta row has already been mirrored to the SQLite cache
        # this run. Mirrors the _flushed_urls delta guard in _write_all_url_snapshot:
        # periodic/per-GIF flushes import only the un-flushed delta instead of the
        # whole (only-growing) dict, collapsing the O(N^2) re-import into O(N).
        # import_meta_dict is ON CONFLICT DO UPDATE / COALESCE, so the terminal
        # full-dict backstops re-write nothing new and the end state is identical.
        self._flushed_meta_pids = set()
        self.url_meta_path = os.path.join(self.path, "all_url_meta.json")
        self._pid_cache_hit = {}
        self._log_step3_cache_detail = False
        self._cookie_requirement_map = {}
        self.revoked_pid_path = os.path.join(self.path, "revoked_pid.txt")
        self._revoked_pid_set = set()
        self._revoked_pid_new = set()
        self._cookie_usage_counts = {"step3": {}, "step4": {}}
        self._cookie_usage_seen = {"step3": set(), "step4": set()}
        self._pending_pid_file_path = os.path.join(self.path, "pictures_id.txt")
        self._pending_pid_lock = threading.Lock()
        self._pending_pid_remaining = set()
        # Per-PID filter-decision cache; cleared by _apply_live_settings_if_changed
        # on a live settings change. Initialized here so the first mid-run change
        # does not hit an AttributeError (only the download thread defines it too).
        self._pid_filter_decision = {}

    def _load_cookie_requirement_cache(self):
        """Populate self._cookie_requirement_map from the saved trace JSON."""
        try:
            req_path = os.path.join(self.path, 'pixiv_cookie_requirement.json')
            req_data = safe_read_json(req_path, {})
            if isinstance(req_data, dict):
                for pid, entry in req_data.items():
                    if isinstance(entry, dict):
                        self._cookie_requirement_map[str(pid)] = entry.get('requires_cookie')
        except Exception:
            self._cookie_requirement_map = {}

    def _emit_step3_init_diag(self):
        """Append a step3_init diagnostic record with the per-filter counts."""
        self._diag(
            "step3_init",
            exist_pid_count=len(self.exist_pid),
            url_meta_count=_safe_meta_count(getattr(self, "_metadata_db", None)),
            like_min=int(self.like_num or 0),
            special_like_rule_count=len(self.special_like_rules),
            ban_tag_count=len(self._ban_tag_norm),
            must_tag_count=len(self._must_tag_norm),
            single_mode=bool(self.single_mode_flag),
        )

    def _diag(self, event, **fields):
        try:
            append_diagnostic_event(self.path, event, stage="step3", **fields)
        except Exception:
            pass

    def _count_text_lines(self, file_path):
        return count_text_lines(file_path)

    def _to_int(self, value, default=None):
        return to_int_lenient(value, default)

    def _set_requires_cookie_meta(self, pid, need_cookie):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            meta = dict(self._get_meta(pid_key))
            meta["requires_cookie"] = need_cookie
            pixiv_info = meta.get("pixiv_info")
            if isinstance(pixiv_info, dict):
                pixiv_info["requires_cookie"] = need_cookie
                meta["pixiv_info"] = pixiv_info
            self.url_meta[pid_key] = meta
        except Exception:
            pass

    def _calc_no_cookie_delay(self):
        """Random delay for a PID that doesn't require a cookie."""
        return pyrandom.randint(self.pid_wait_nocookie_min, self.pid_wait_nocookie_max)

    def _calc_cookie_required_delay(self):
        """Random delay for cookie-required PIDs in the no-scheduler fallback path.

        Live-reads pid_cooldown_avg from settings and applies ±30% jitter
        (same model as AccountScheduler.release).
        """
        try:
            from app.core.settings_store import SettingsStore
            import os
            store_path = os.getenv("APPDATA") + r"/pixiv_download/"
            avg = int(SettingsStore(store_path).get_section("performance").get("pid_cooldown_avg", 35))
        except Exception:
            avg = 35
        low = max(1, int(avg * 0.7))
        high = max(low, int(avg * 1.3))
        return pyrandom.randint(low, high)

    def _sleep_ultra_slow(self, pid, need_cookie=None):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            if bool(self._pid_cache_hit.get(pid_key, False)):
                return
        except Exception:
            pass
        # Scheduler handles inter-PID cooldown via release(); skip the legacy sleep.
        if self._scheduler is not None:
            return
        try:
            self._countdown_pid = pid_key
        except Exception:
            pass
        delay = (
            self._calc_no_cookie_delay()
            if need_cookie is False
            else self._calc_cookie_required_delay()
        )
        self._sleep_with_countdown(delay)

    def _cookie_label_from_alias_selection(self, pid_key):
        """Try the per-PID alias map first (set by _select_cookie_for_pid)."""
        try:
            alias = str(self._pid_cookie_alias_selection.get(pid_key, "") or "").strip()
            return alias or None
        except Exception:
            return None

    def _cookie_label_from_pid_selection(self, pid_key):
        """Resolve the label for the cookie remembered for this PID."""
        try:
            selected = str(self._pid_cookie_selection.get(pid_key, "") or "").strip()
            if not selected:
                return None
            resolved = cookie_usage_label(selected, self.cookie_pool, self._cookie_alias_map)
            return resolved or None
        except Exception:
            return None

    def _cookie_label_from_pool_first(self):
        """Fallback: label of the pool's first cookie."""
        try:
            if not self.cookie_pool:
                return None
            resolved = cookie_usage_label(
                self.cookie_pool[0], self.cookie_pool, self._cookie_alias_map,
            )
            return resolved or None
        except Exception:
            return None

    def _cookie_label_default(self, need_cookie):
        """Final fallback when no cookie source is available."""
        single_cookie = str(getattr(self, "cookies", "") or "").strip()
        if need_cookie is False and not single_cookie:
            return "免Cookie"
        if single_cookie:
            return "單一Cookie"
        return "未提供Cookie"

    def _cookie_label_for_pid(self, pid, need_cookie=None):
        pid_key = normalize_pid(pid) or str(pid)
        for resolver in (
            lambda: self._cookie_label_from_alias_selection(pid_key),
            lambda: self._cookie_label_from_pid_selection(pid_key),
            lambda: self._cookie_label_from_pool_first(),
        ):
            label = resolver()
            if label:
                return label
        return self._cookie_label_default(need_cookie)


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

    def _stamp_gif_cookie_usage_in_meta(self, pid_key, source):
        """Mark requires_cookie + cookie_used fields on the in-memory url_meta entry."""
        try:
            self._set_requires_cookie_meta(pid_key, True)
            meta = dict(self._get_meta(pid_key))
            meta["cookie_used"] = True
            meta["cookie_used_source"] = str(source)
            meta["cookie_used_updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.url_meta[pid_key] = meta
        except Exception:
            pass

    def _emit_gif_cookie_usage_signal(self, pid_key, source):
        """Best-effort emit via the legacy ._output signal (Qt5 compat)."""
        signal_obj = self.__dict__.get("_output", None)
        if signal_obj is None:
            try:
                signal_obj = getattr(self, "_output", None)
            except Exception:
                signal_obj = None
        try:
            if signal_obj is not None and hasattr(signal_obj, "emit"):
                signal_obj.emit(
                    f"<p><font color='blue'>[GIF][Cookie] PID {pid_key} "
                    f"使用 cookies（來源：{source}），已更新 all_url_meta 暫存</font></p>"
                )
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
        self._stamp_gif_cookie_usage_in_meta(pid_key, source)
        self._persist_url_meta_with_fallback(pid_key=pid_key)
        self._emit_gif_cookie_usage_signal(pid_key, source)

    def _on_pause_hook(self):
        self._flush_url_meta_snapshot()

    def _on_stop_hook(self):
        self._flush_url_meta_snapshot(full=True)

    def flush_for_shutdown(self):
        """Synchronously persist in-flight metadata and close SQLite for window-close."""
        try:
            self._flush_url_meta_snapshot(full=True)
        except Exception:
            pass
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    def _check_exist_candidate_paths(self):
        """Return the list of pictures_id.txt paths to try, in priority order."""
        candidates = [os.path.join(self.path, "pictures_id.txt")]
        try:
            appdata_path = os.path.join(os.getenv('APPDATA') + r'/pixiv_download/', 'pictures_id.txt')
            if appdata_path not in candidates:
                candidates.append(appdata_path)
        except Exception:
            pass
        return candidates

    def _load_check_exist_block_set(self):
        """Build the PID set that should be excluded from Step 3 (no_to_check)."""
        try:
            if isinstance(self.no_to_check, list):
                return normalize_pid_set(self.no_to_check)
        except Exception:
            pass
        return set()

    def _load_step2_skip_set(self):
        """Load PIDs that Step 2 already filed as 'skip' (so we won't re-fetch)."""
        skip_file = os.path.join(self.path, "step2_skip_pid.txt")
        try:
            if os.path.isfile(skip_file):
                with open(skip_file, encoding="utf-8", errors="ignore") as f:
                    return normalize_pid_set(
                        [line.rstrip() for line in f if str(line).strip()]
                    )
        except Exception:
            pass
        return set()

    def _scan_pictures_id_lines(self, file_iter, block_set, step2_skip_set):
        """Walk an iterator of pictures_id lines and partition them.

        Returns ``(pids, raw_count, excluded_by_block, excluded_by_step2_skip)``.
        Pulled out so the caller can try strict-UTF-8 first, then fall back to
        a lenient re-read on UnicodeDecodeError without duplicating the logic.
        """
        pictures_id = []
        excluded_by_skip_file = 0
        excluded_by_step2_skip = 0
        raw_count = 0
        for line in file_iter:
            text = str(line).strip()
            if not text:
                continue
            raw_count += 1
            pid_key = normalize_pid(text)
            if pid_key and pid_key in block_set:
                excluded_by_skip_file += 1
                if pid_key in step2_skip_set:
                    excluded_by_step2_skip += 1
                continue
            pictures_id.append(text)
        return pictures_id, raw_count, excluded_by_skip_file, excluded_by_step2_skip

    def _scan_pictures_id_file(self, pic_path, block_set, step2_skip_set):
        """Stream-parse pictures_id.txt with UTF-8 strict, then lenient fallback."""
        try:
            with open(pic_path, encoding='utf-8') as f:
                return self._scan_pictures_id_lines(f, block_set, step2_skip_set)
        except UnicodeDecodeError:
            with open(pic_path, encoding='utf-8', errors='ignore') as f:
                return self._scan_pictures_id_lines(f, block_set, step2_skip_set)

    def _emit_check_exist_summary(self, pic_path, pictures_id, raw, blocked, step2_blocked):
        """Surface the parse summary to the user and append to the diagnostic log."""
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>pictures_id 來源: {pic_path}</font></p>"))
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[TaskFilter][Step3-Pre] pictures_id原始={raw}, "
                f"skip_file排除={blocked}, 待去重={len(pictures_id)}</font></p>"
            ))
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[TaskFilter][Step3-Pre] 這次從 step2_skip_pid.txt "
                f"排除 {step2_blocked} 筆（步驟2提前跳過，所以之前沒有存下來）</font></p>"
            ))
        except Exception:
            pass
        self._diag(
            "step3_skip_file_prefilter",
            source_path=str(pic_path),
            raw_count=int(raw),
            skipped_no_to_check=int(blocked),
            skipped_step2_skip_file=int(step2_blocked),
            post_skip_file_count=int(len(pictures_id)),
        )

    def _emit_check_exist_failure(self, candidates, last_err):
        """Notify the user and shut down Step 3 when no candidate file was readable."""
        detail = "" if last_err is None else f" ({last_err})"
        try:
            self._q.put(WorkerEvent("output",
                "<p><font color='red'>找不到 pictures_id.txt: {}</font></p>".format(
                    ' | '.join(candidates))))
            self._q.put(WorkerEvent("output",
                f"<p><font color='red'>讀取失敗: {detail}</font></p>"))
        except Exception:
            pass
        self._q.put(WorkerEvent("finished", 'Task finished'))
        self._q.put(WorkerEvent("next", -1))

    def check_exist(self):
        block_set = self._load_check_exist_block_set()
        step2_skip_set = self._load_step2_skip_set()
        # DB-first: prefer pending_pids table when it has rows
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            try:
                db_pids = db.get_pending_pids()
                if db_pids:
                    pictures_id, raw, blocked, step2_blocked = self._scan_pictures_id_lines(
                        db_pids, block_set, step2_skip_set
                    )
                    self._emit_check_exist_summary(
                        "DB:pending_pids", pictures_id, raw, blocked, step2_blocked
                    )
                    return pictures_id
            except Exception:
                pass
        # Fallback: file
        file_candidates = self._check_exist_candidate_paths()
        last_err = None
        for pic_path in file_candidates:
            if not os.path.isfile(pic_path):
                continue
            try:
                pictures_id, raw, blocked, step2_blocked = self._scan_pictures_id_file(
                    pic_path, block_set, step2_skip_set
                )
                self._emit_check_exist_summary(
                    pic_path, pictures_id, raw, blocked, step2_blocked
                )
                return pictures_id
            except Exception as err:
                last_err = err
        self._emit_check_exist_failure(file_candidates, last_err)
        return 0

    def _lookup_url_meta_entry(self, pid_key):
        """Read self.url_meta[pid_key] safely; returns ``None`` for any failure."""
        try:
            meta = self._get_meta(pid_key)
        except Exception:
            return None
        if not meta:
            return None
        return meta

    def _meta_has_usable_url_and_pages(self, meta):
        img_url = str(meta.get("img_url", "") or "").strip()
        if not img_url or img_url == "None":
            return False
        pagecount = self._to_int(meta.get("pagecount", 0), 0) or 0
        return pagecount > 0

    def _is_pid_cached_meta(self, pid):
        pid_key = normalize_pid(pid) or str(pid)
        meta = self._lookup_url_meta_entry(pid_key)
        if meta is None:
            return False, {}
        if not self._meta_has_usable_url_and_pages(meta):
            return False, meta
        return True, meta

    @staticmethod
    def _expand_img_url_to_pages(img_url, page_total):
        """Expand a meta img_url ('..._p.jpg', '..._p0.jpg', '..._ugoira0.zip') into
        N per-page URLs. Returns [] when the URL has no extension separator."""
        if not img_url or "." not in img_url:
            return []
        if page_total < 1:
            page_total = 1
        left, right = img_url.rsplit(".", 1)
        # Normalise trailing "_pN" / "_ugoiraN" → "_p" / "_ugoira" so we can append idx.
        left_norm = re.sub(r"_(p|ugoira)\d+$", r"_\1", left, flags=re.IGNORECASE)
        if re.search(r"_(p|ugoira)$", left_norm, flags=re.IGNORECASE):
            return [left_norm + str(idx) + "." + right for idx in range(page_total)]
        # Fallback to legacy behavior (suffix on raw stem).
        return [left + str(idx) + "." + right for idx in range(page_total)]

    def _build_cached_urls_from_meta(self, pid, meta):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            img_url = str((meta or {}).get("img_url", "") or "").strip()
            page_total = self._to_int((meta or {}).get("pagecount", 1), 1) or 1
            return self._expand_img_url_to_pages(img_url, page_total)
        except Exception:
            try:
                self._diag("step3_cached_url_build_failed", pid=str(pid_key))
            except Exception:
                pass
            return []

    def _refresh_cookie_requirement_for_cached(self, pid_key, meta):
        """Re-check requires_cookie for a cache-hit PID and stamp it on url_meta."""
        try:
            need_cookie = self._refresh_cookie_requirement(
                pid_key,
                fallback=(meta.get("requires_cookie") if isinstance(meta, dict) else None),
            )
        except Exception:
            need_cookie = None
        try:
            if isinstance(self.url_meta.get(pid_key), dict):
                self._set_requires_cookie_meta(pid_key, need_cookie)
        except Exception:
            pass
        return need_cookie

    def _record_cached_filter_decision(self, pid_key, passed, reason):
        """Stamp the artwork-filter outcome on the meta entry."""
        try:
            if isinstance(self.url_meta.get(pid_key), dict):
                self.url_meta[pid_key]["filter_pass"] = bool(passed)
                self.url_meta[pid_key]["filter_reason"] = str(reason)
        except Exception:
            pass

    def _prefilter_one_pid_with_cache(self, pid_key, stats):
        """Try to satisfy one PID from cache. Returns ``(cached_urls, needs_network)``."""
        has_cache, meta = self._is_pid_cached_meta(pid_key)
        if not has_cache:
            self._pid_cache_hit[pid_key] = False
            return [], True

        self._pid_cache_hit[pid_key] = True
        self._refresh_cookie_requirement_for_cached(pid_key, meta)

        tag = meta.get("tag", []) if isinstance(meta, dict) else []
        like = meta.get("like", 0) if isinstance(meta, dict) else 0
        passed, reason = self._passes_artwork_filters(pid_key, tag, like)
        self._record_cached_filter_decision(pid_key, passed, reason)
        if not passed:
            stats["cached_filtered"] += 1
            return [], False

        one_pid_urls = self._build_cached_urls_from_meta(pid_key, meta)
        if not one_pid_urls:
            self._pid_cache_hit[pid_key] = False
            stats["cached_fallback_network"] += 1
            return [], True

        stats["cached_hit_pid"] += 1
        stats["cached_generated_url"] += len(one_pid_urls)
        return one_pid_urls, False

    def _prefilter_step3_with_cache(self, pending_pids):
        network_pids = []
        cached_urls = []
        stats = {
            "cached_hit_pid": 0,
            "cached_generated_url": 0,
            "cached_filtered": 0,
            "cached_fallback_network": 0,
        }
        for raw_pid in pending_pids:
            pid_key = normalize_pid(raw_pid) or str(raw_pid)
            urls, needs_network = self._prefilter_one_pid_with_cache(pid_key, stats)
            if urls:
                cached_urls.extend(urls)
            if needs_network:
                network_pids.append(pid_key)
        return network_pids, cached_urls, stats

    def _prepare_pending_pid_tasks(self, raw_pictures_id):
        pending = []
        seen = set()
        skipped_no_to_check = 0
        skipped_exist = 0
        skipped_revoked = 0
        duplicate = 0
        invalid = 0
        try:
            no_to_check_set = normalize_pid_set(self.no_to_check)
        except Exception:
            no_to_check_set = set()
        for raw in raw_pictures_id:
            pid = normalize_pid(raw)
            if not pid:
                invalid += 1
                continue
            if pid in no_to_check_set:
                skipped_no_to_check += 1
                continue
            if pid in seen:
                duplicate += 1
                continue
            seen.add(pid)
            if pid in self.exist_pid:
                skipped_exist += 1
                continue
            if pid in self._revoked_pid_set:
                skipped_revoked += 1
                continue
            pending.append(pid)
        return pending, skipped_no_to_check, skipped_exist, skipped_revoked, duplicate, invalid
    def _reset_run_counters(self):
        self._step3_query_counts = {"network": 0, "cache": 0, "skip": 0}
        self._step3_cookie_req_counts = {"need": 0, "free": 0, "unknown": 0}
        self._step3_wait_applied_count = 0

    def _load_and_filter_pid_list(self):
        self._emit_output("<p><font color='gray'>[Step3] 讀取 pictures_id.txt 並比對既有檔案...</font></p>")
        pictures_id = self.check_exist()
        if not isinstance(pictures_id, list):
            self._q.put(WorkerEvent("output", "<p><font color='red'>pictures_id 讀取失敗，無法開始 URL 階段</font></p>"))
            self._diag("step3_abort_no_pictures_id")
            self._q.put(WorkerEvent("next", -1))
            return None
        raw_pid_count = len(pictures_id)
        pictures_id, skipped_no_to_check, skipped_exist, skipped_revoked, duplicate_count, invalid_count = self._prepare_pending_pid_tasks(pictures_id)
        self._init_pending_pid_tracker(pictures_id, reset_with_fallback=True)
        self.pid_max = len(pictures_id)
        try:
            self._q.put(WorkerEvent("progress", (0, self.pid_max)))
        except Exception:
            pass
        self._diag(
            "step3_task_filter",
            input_count=raw_pid_count,
            skipped_no_to_check=skipped_no_to_check,
            skipped_exist=skipped_exist,
            skipped_revoked=skipped_revoked,
            duplicate_count=duplicate_count,
            invalid_count=invalid_count,
            cached_hit_pid=0,
            cached_generated_url=0,
            cached_filtered=0,
            cached_fallback_network=0,
            pending_count=self.pid_max,
        )
        self._emit_output(f"<p><font color='gray'>[TaskFilter][Step3] input={raw_pid_count}, skipped_no_to_check={skipped_no_to_check}, skipped_exist={skipped_exist}, skipped_revoked={skipped_revoked}, duplicate={duplicate_count}, invalid={invalid_count}, cached_hit_pid={0}, cached_generated_url={0}, cached_filtered={0}, cached_fallback_network={0}, pending_network={self.pid_max}</font></p>")
        self._q.put(WorkerEvent("output", f"<p><font color='red'>Total pending network PID: {self.pid_max}</font></p>"))
        try:
            self._q.put(WorkerEvent("output", "<p><font color='gray'>URL 輸出檔案: {}</font></p>".format(os.path.join(self.path, "all_url.txt"))))
        except Exception:
            pass
        if self.pid_max == 0:
            self._q.put(WorkerEvent("output", "<p><font color='orange'>pictures_id.txt 目前為空，沒有可處理 PID</font></p>"))
            self._emit_cookie_usage_summary("step3", "Step3 Cookie統計")
            self._q.put(WorkerEvent("next", 4))
            return None
        return pictures_id

    def _build_and_emit_task_queue(self, pictures_id):
        self._q.put(WorkerEvent("output",
            f"<p><font color='green'>URL階段等待策略：僅網路查詢PID等待；快取PID不等待（免Cookie {self.pid_wait_nocookie_min}~{self.pid_wait_nocookie_max} 秒；Scheduler 管理跨PID冷卻）</font></p>"
        ))
        if self._scheduler is not None:
            try:
                self._q.put(WorkerEvent("output",
                    f"<p><font color='gray'>URL階段已啟用 AccountScheduler，{len(self.cookie_pool or [])} 組 cookies 輪替</font></p>"
                ))
            except Exception:
                pass
        task_queue = Queue()
        for pid in pictures_id:
            task_queue.put(pid)
        self._q.put(WorkerEvent("output", "<p><font color='green'>URL階段採用消費者隊列模式，PID 會在查詢後自動從 pictures_id.txt 移除</font></p>"))
        return task_queue

    def _emit_pid_detail_log(self, pid, processed_count):
        """Per-PID detail line emitted after get_download_url resolves.

        Pulls tag/like/pagecount/requires_cookie from self.url_meta[pid]
        and the query source from pixiv_info.source. If url_meta has no
        entry (revoked / exist_pid / network fail), emit a generic line so
        every PID still produces visible output.
        """
        try:
            pid_key = normalize_pid(pid) or str(pid)
            meta = self.url_meta.get(pid_key) if isinstance(self.url_meta, dict) else None
            if isinstance(meta, dict):
                pinfo = meta.get("pixiv_info") if isinstance(meta.get("pixiv_info"), dict) else {}
                source = pinfo.get("source") or "unknown"
                source_label = {
                    "cache": "快取", "network": "網路", "migrated": "舊資料",
                    "skip": "略過", "unknown": "未知",
                }.get(str(source), str(source))
                like = meta.get("like", "?")
                pagecount = meta.get("pagecount", "?")
                tags = meta.get("tag", []) if isinstance(meta.get("tag", []), list) else []
                tag_count = len(tags)
                tag_preview = "、".join(str(t) for t in tags[:3])
                if tag_count > 3:
                    tag_preview += "…"
                req = meta.get("requires_cookie", None)
                req_label = "需要" if req is True else ("不需要" if req is False else "未知")
                filter_pass = meta.get("filter_pass", None)
                filter_reason = meta.get("filter_reason", "")
                if filter_pass is False:
                    reason_label = {
                        "ban_tag": "命中封鎖標籤", "must_tag": "缺少必含標籤", "like": "愛心數不足",
                    }.get(str(filter_reason), str(filter_reason) or "未通過")
                    color = "orange"
                    extra = f"，過濾未通過（{reason_label}）"
                else:
                    color = "black"
                    extra = ""
                tag_text = f"標籤 {tag_count} 個" + (f"（{tag_preview}）" if tag_preview else "")
                user_name = meta.get("user_name")
                by_label = f" [by {user_name}]" if user_name else ""
                self._q.put(WorkerEvent("output",
                    f"<p><font color='{color}'>處理 PID {processed_count + 1}/{self.pid_max}："
                    f"{pid_key}{by_label}（來源={source_label}、愛心={like}、頁數={pagecount}、"
                    f"{tag_text}、cookie={req_label}）{extra}</font></p>"))
            else:
                self._q.put(WorkerEvent("output",
                    f"<p><font color='gray'>處理 PID {processed_count + 1}/{self.pid_max}："
                    f"{pid_key}（無 url_meta 資料，可能已失效或已存在）</font></p>"))
        except Exception:
            pass

    def _fetch_one_pid_via_scheduler(self, pid):
        """Acquire account → fetch with retry → release. Returns (one, stop).

        Mirrors Step 4's try/finally release contract: the held account is
        ALWAYS released (even if a non-network exception escapes the retry
        lambda, which would otherwise leak ``held=True`` and hang the pool on a
        small cookie set), a user Stop releases neutrally (no disable / no
        success credit), and the per-PID session is always closed (was leaked
        once per PID — ~1.1M abandoned sessions on the real dataset)."""
        acc = self._acquire_account()
        if acc is None:
            return None, True  # stop signal or all disabled
        session = pixiv_api.make_session(acc.proxy_url)
        ok = False
        neutral = False
        try:
            ok, one, _ = self._run_with_network_retry(
                f"PID {pid}",
                lambda: self.get_download_url(
                    self.path, self.Agent, 1, pid,
                    cookie_override=acc.cookie, session=session,
                ),
            )
            return one, False
        except Exception:
            neutral = True  # non-network failure: not the cookie's fault
            raise
        finally:
            with contextlib.suppress(Exception):
                session.close()
            self._release_account_after_work(acc, ok=ok, neutral=neutral)

    @staticmethod
    def _normalize_loop_result(one):
        """Translate get_download_url's polymorphic return into a list-of-URL list."""
        if isinstance(one, list):
            return one
        if isinstance(one, str) and one.startswith('http'):
            return [one]
        return []

    def _flush_loop_batch(self, results, processed_count):
        """Per-100-PID batch flush: snapshot all_url, persist meta + pending PIDs, log."""
        flat_results = [x for item in results if isinstance(item, list) for x in item]
        old_urls, new_urls, merged = self._write_all_url_snapshot(flat_results)
        self._flush_url_meta_snapshot()
        self._persist_pending_pid_file()  # Phase 36: batch-aligned with all_url flush
        self._diag(
            "step3_batch_flush",
            index=processed_count,
            total=self.pid_max,
            batch_total_urls=len(flat_results),
            merged_count=len(merged),
            added_count=len(new_urls),
        )
        try:
            remain = len(self._pending_pid_remaining or set())
        except Exception:
            remain = 0
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[分批寫入] 已處理 {processed_count}/"
                f"{self.pid_max}，all_url 目前 {len(merged)} 筆（本批新增 "
                f"{len(new_urls)}），pictures_id 剩餘 {remain}</font></p>"))
        except Exception:
            pass

    def _run_processing_loop(self, task_queue):
        results = []
        processed_count = 0
        # 25 顆粒：100 太肉，崩潰時可能丟掉 5–10 分鐘工作；25 約 1–2 分鐘
        flush_every = 25

        while (not task_queue.empty()) and not self._stop_event.is_set():
            try:
                pid = task_queue.get_nowait()
            except Exception:
                break

            # Pick up any mid-run 「儲存設定」 before filtering/querying this PID.
            self._apply_live_settings_if_changed()

            with contextlib.suppress(Exception):
                self._q.put(WorkerEvent("phase", f"正在查詢：PID {pid}"))

            if self._scheduler is not None:
                one, stop = self._fetch_one_pid_via_scheduler(pid)
                if stop:
                    break
            else:
                one = self.get_download_url(self.path, self.Agent, 1, pid)

            self._emit_pid_detail_log(pid, processed_count)

            results.append(self._normalize_loop_result(one))

            if not self._stop_event.is_set():
                self._mark_pid_processed(pid, one)

            processed_count += 1
            task_queue.task_done()

            if processed_count % flush_every == 0 or processed_count == self.pid_max:
                self._flush_loop_batch(results, processed_count)

        return results

    def _finalize_on_stop(self, results, exhausted=False):
        """Persist pending state and end WITHOUT chaining into Step 4.

        Used for both a user Stop and a cookie-pool exhaustion (``exhausted``):
        in either case Step 3 did NOT finish the PID list, so emitting the
        terminal ``next == -1`` (instead of ``next == 4``) stops Run All from
        advancing into Step 4 on a truncated URL set. The remaining PIDs survive
        in pictures_id.txt for the next run."""
        try:
            flat_results = [x for item in results if isinstance(item, list) for x in item]
            old_urls, new_urls, merged = self._write_all_url_snapshot(flat_results, full=True)
            self._flush_url_meta_snapshot(full=True)
            self._persist_pending_pid_file()  # Phase 36: ensure pending list is saved on stop
            self._flush_revoked_pid_file()
            self._emit_step3_filter_skip_final_summary()
            self._emit_step3_query_final_summary(final=True)
            self._emit_cookie_usage_summary("step3", "Step3 Cookie統計")
            self._diag(
                "step3_stopped",
                reason="cookies_exhausted" if exhausted else "user_stop",
                collected_url_count=len(flat_results),
                merged_count=len(merged),
                added_count=len(new_urls),
            )
            if exhausted:
                msg = (f"<p><font color='orange'>所有 Cookie 已禁用，URL階段未完成；"
                       f"已暫存 all_url {len(merged)} 筆（新增 {len(new_urls)}），"
                       f"剩餘 PID 保留待下次重跑</font></p>")
            else:
                msg = f"<p><font color='orange'>已停止，已暫存 all_url {len(merged)} 筆（新增 {len(new_urls)}）</font></p>"
            self._q.put(WorkerEvent("output", msg))
        except Exception:
            pass
        self._q.put(WorkerEvent("finished", 'Task finished'))
        self._q.put(WorkerEvent("next", -1))

    def _step3_emit(self, html):
        """Best-effort progress log emit during Step 3 finalize."""
        try:
            self._q.put(WorkerEvent("output", html))
        except Exception:
            pass

    def _split_results_and_errors(self, results):
        """Flatten the worker results list and split URL strings from error PIDs."""
        flat = [i for item in results if isinstance(item, list) for i in item]
        error_pid = [i for i in flat if 'https' not in i]
        url_results = [i for i in flat if 'https' in i]
        return url_results, error_pid

    def _emit_step3_summaries(self):
        """Run the trailing summary emitters in the order the user expects."""
        self._flush_revoked_pid_file()
        self._emit_step3_filter_skip_final_summary()
        self._emit_step3_query_final_summary(final=True)
        self._emit_cookie_usage_summary("step3", "Step3 Cookie統計")
        free = int(self._step3_cookie_req_counts.get("free", 0))
        if free > 0:
            self._emit_output(f"<p><font color='teal'>免 Cookie 查詢：{free} 次</font></p>")

    def _finalize_on_complete(self, results):
        self._step3_emit("<p><font color='gray'>[Step3完成] 整理結果並寫入 all_url.txt...</font></p>")
        url_results, error_pid = self._split_results_and_errors(results)
        old_urls, new_urls, merged = self._write_all_url_snapshot(url_results, full=True)

        self._step3_emit("<p><font color='gray'>[Step3完成] 持久化剩餘 pending PID...</font></p>")
        self._persist_pending_pid_file()  # Phase 36: flush remaining pending PIDs

        self._diag(
            "step3_completed",
            old_count=len(old_urls),
            new_count=len(new_urls),
            merged_count=len(merged),
            error_pid_count=len(error_pid),
        )
        self._step3_emit(
            f"<p><font color='green'>all_url 寫入完成：舊URL {len(old_urls)} 筆、"
            f"新URL {len(new_urls)} 筆、合併後 {len(merged)} 筆</font></p>"
        )
        if not new_urls:
            self._step3_emit("<p><font color='gray'>沒有新的 URL，已保留原 all_url.txt</font></p>")

        _mc = _safe_meta_count(getattr(self, "_metadata_db", None))
        self._step3_emit(
            f"<p><font color='gray'>[Step3完成] 寫入 metadata DB（{_mc} 筆）...</font></p>"
        )
        self._persist_step3_url_meta()

        self._step3_emit("<p><font color='gray'>[Step3完成] 附加 tag_ban / pid_num / net_err 紀錄...</font></p>")
        self._drain_queue_to_text_file(self.tag_queue, "tag_ban_pid.txt", mode_append=True)
        self._drain_queue_to_text_file(self.like_queue, "pid_num_pid.txt", mode_append=True)
        self._persist_step3_net_err(error_pid)

        self._emit_step3_summaries()
        if self._revoked_pid_new:
            self._step3_emit(
                f"<p><font color='orange'>本次新增失效 PID "
                f"{len(self._revoked_pid_new)} 筆，已寫入 revoked_pid.txt</font></p>"
            )
        self._q.put(WorkerEvent("finished", '抓取所有PID完成'))
        self._q.put(WorkerEvent("next", 4))
        self._q.put(WorkerEvent("output",
            f"<p><font color='red'>Total URL count: {len(merged)}</font></p>"))

    def run(self):
        try:
            self._q.put(WorkerEvent("output", "URL階段開始"))
            self._reset_run_counters()
            pictures_id = self._load_and_filter_pid_list()
            if pictures_id is None:
                return
            task_queue = self._build_and_emit_task_queue(pictures_id)
            results = self._run_processing_loop(task_queue)
            # The loop also breaks when the scheduler's whole cookie pool got
            # disabled (acquire() returns None for BOTH stop AND all-disabled).
            # That is NOT a clean completion: finishing here and chaining into
            # Step 4 would download only the truncated set already resolved.
            pool_exhausted = (
                not self._stop_event.is_set()
                and self._scheduler is not None
                and getattr(self._scheduler, "all_disabled", lambda: False)()
            )
            if self._stop_event.is_set() or pool_exhausted:
                self._finalize_on_stop(results, exhausted=pool_exhausted)
            else:
                self._finalize_on_complete(results)
        except Exception as e:
            self._diag("step3_exception", error=output_err(e))
            self._q.put(WorkerEvent("output", 'Task failed'))
            self._q.put(WorkerEvent("output", output_err(e)))
            self._q.put(WorkerEvent("next", -1))

    def _step3_advance_progress(self):
        """Advance step 3 progress counter and emit progress signal unless stopped.

        Mirrors the repeated `if self._isPause!=2: self.pid_now+=1; self._signal.emit(1,self.pid_max)`
        pattern used at the end of every PID resolution branch in `get_download_url`.
        """
        if not self._stop_event.is_set():
            self.pid_now = self.pid_now + 1
            self._q.put(WorkerEvent("progress", (1, self.pid_max)))

    def _step3_extract_meta_from_cache(self, cached):
        """Unpack a cached url_meta entry into the 9-element meta tuple expected
        by ``get_download_url``: ``(tag, like, pagecount, img_url, need_cookie,
        upload_date, create_date, user_id, user_name)``. The 4 trailing fields
        default to ``None`` for entries that predate the schema extension —
        Step 3 will re-fetch via network when the cache is missing img_url, so
        those NULLs only affect filter-passing artwork that already has cached
        urls (acceptable: existing rows in DB stay NULL until next re-fetch)."""
        tag = cached.get('tag', [])
        like = cached.get('like', 0)
        pagecount = int(cached.get('pagecount', 1) or 1)
        img_url = cached.get('img_url')
        need_cookie = cached.get('requires_cookie', None)
        upload_date = cached.get('upload_date')
        create_date = cached.get('create_date')
        user_id = cached.get('user_id')
        user_name = cached.get('user_name')
        return (tag, like, pagecount, img_url, need_cookie,
                upload_date, create_date, user_id, user_name)

    def _step3_safe_emit(self, html):
        """Emit `html` on `_output`, swallowing any signal-emit exception.

        Mirrors the inline `try: self._q.put(WorkerEvent("output", ...) except Exception: pass` blocks)
        that appear repeatedly inside `get_download_url`."""
        try:
            self._q.put(WorkerEvent("output", html))
        except Exception:
            pass

    def _step3_finalize_query(self, ret_value, query_source, need_cookie, wait_applied):
        """Record the step 3 query outcome and return `ret_value`.

        Extracted from the `_finalize` closure inside `get_download_url`. Wraps
        `_record_step3_query_result` in try/except so any bookkeeping failure does not
        propagate (matches original behavior).
        """
        try:
            self._record_step3_query_result(
                query_source,
                need_cookie=need_cookie,
                wait_applied=bool(wait_applied),
            )
        except Exception:
            pass
        return ret_value

    def _step3_safe_cookie_requirement(self, pid_key):
        """Return the persisted cookie-requirement fallback for pid_key (or None on any error).
        Mirrors the inline try/except previously inside `get_download_url`."""
        try:
            return self._cookie_requirement_map.get(pid_key, None)
        except Exception:
            return None

    def _step3_record_filter_result(self, pid_key, passed, reason):
        """Annotate the url_meta entry for pid_key with the filter outcome.
        Best-effort: any exception is swallowed (matches the original inline try/except)."""
        try:
            if isinstance(self.url_meta.get(pid_key), dict):
                self.url_meta[pid_key]["filter_pass"] = bool(passed)
                self.url_meta[pid_key]["filter_reason"] = str(reason)
        except Exception:
            pass

    def _step3_build_pid_download_urls(self, img_url, pagecount):
        """Construct the multi-page download URL list for a PID.

        Returns a list[str] of per-page URLs on success, or None if the original
        try/except in `get_download_url` would have caught an exception (caller then
        takes the `[str(pid_key)]` skip-and-record-pid path).
        """
        try:
            img = str(img_url).rsplit(".", 1)
            page_total = int(pagecount) if int(pagecount) > 0 else 1
            urls = []
            for count in range(0, page_total):
                urls.append(img[0] + str(count) + "." + img[1])
            return urls
        except Exception:
            return None

    def _step3_build_url_meta_entry(self, pid_key, tag, like, pagecount, img_url,
                                     need_cookie, artwork_url, query_source,
                                     upload_date=None, create_date=None,
                                     user_id=None, user_name=None):
        """Write the url_meta entry for a successfully resolved PID. Wrapped in try/except
        to preserve the original best-effort semantics (failures must not abort the workflow).

        ``upload_date`` / ``create_date`` are Pixiv's ISO-8601 timestamps;
        ``user_id`` / ``user_name`` identify the artist. ``user_id`` may already
        be set in the DB by Step 2 — we still write it here for the JSON cache
        so :meth:`mirror_meta_dict_to_db` propagates it on shutdown.
        """
        try:
            self.url_meta[pid_key] = {
                "tag": tag if isinstance(tag, list) else [],
                "like": int(like) if str(like).isdigit() else like,
                "pagecount": int(pagecount) if str(pagecount).isdigit() else pagecount,
                "img_url": img_url,
                "requires_cookie": need_cookie,
                "artwork_url": artwork_url,
                "upload_date": upload_date,
                "create_date": create_date,
                "user_id": user_id,
                "user_name": user_name,
                "pixiv_info": {
                    "tag": tag if isinstance(tag, list) else [],
                    "like": int(like) if str(like).isdigit() else like,
                    "pagecount": int(pagecount) if str(pagecount).isdigit() else pagecount,
                    "img_url": img_url,
                    "requires_cookie": need_cookie,
                    "queried_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source": query_source,
                    "upload_date": upload_date,
                    "create_date": create_date,
                    "user_id": user_id,
                    "user_name": user_name,
                },
            }
        except Exception:
            pass

    def _step3_fetch_artwork_info(self, url, Agent, pid_key, pid_cookie, session):
        """Network fetch for one artwork. Returns the raw Pixiv_info result.

        ``ProxyError``/``ConnectTimeout``/``ConnectionError`` propagate so the
        scheduler-aware caller can disable the cookie via release(ok=False).
        Other exceptions are surfaced to the user and re-raised.
        """
        try:
            if pid_cookie:
                return Pixiv_info(url, Agent=Agent, cookie=pid_cookie, session=session)
            return Pixiv_info(url, Agent=Agent, session=session)
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError):
            raise
        except Exception as e:
            self._step3_safe_emit(
                f"<p><font color='red'>PID {pid_key} 取得資訊失敗：{e}</font></p>"
            )
            raise

    def _step3_resolve_meta_from_cache(self, pid_key, cached):
        """Pull the 9-element meta tuple out of the cache entry."""
        self._pid_cache_hit[pid_key] = True
        (tag, like, pagecount, img_url, need_cookie,
         upload_date, create_date, user_id, user_name) = (
            self._step3_extract_meta_from_cache(cached)
        )
        need_cookie = self._refresh_cookie_requirement(pid_key, fallback=need_cookie)
        if self.single_mode_flag and bool(getattr(self, "_log_step3_cache_detail", False)):
            self._step3_safe_emit(
                f"<p><font color='gray'>[URL階段] PID {pid_key} 使用本地快取，跳過等待</font></p>"
            )
        return (tag, like, pagecount, img_url, need_cookie,
                upload_date, create_date, user_id, user_name)

    def _step3_resolve_meta_from_network(
        self, pid_key, url, Agent, session, cookie_override
    ):
        """Network fetch + parse for one PID. Returns the 9-element meta tuple
        ``(tag, like, pagecount, img_url, need_cookie, upload_date, create_date,
        user_id, user_name)`` on success, or ``None`` when the call failed /
        404'd / returned a malformed payload."""
        self._pid_cache_hit[pid_key] = False
        if cookie_override is not None:
            pid_cookie = cookie_override
        else:
            pid_cookie = self._select_cookie_for_pid(pid_key)
        label = self._record_cookie_usage("step3", pid_key, pid_cookie)
        try:
            info = self._step3_fetch_artwork_info(url, Agent, pid_key, pid_cookie, session)
            if self._stats_collector is not None:
                self._stats_collector.report_request(label)
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError):
            raise
        except Exception:
            return None
        if info == [404]:
            self._mark_revoked_pid(pid_key, reason="404")
            return None
        try:
            (tag, like, pagecount, img_url,
             upload_date, create_date, user_id, user_name) = info
        except Exception:
            return None
        # Empty img_url on an otherwise-parsed 200 body (age-gated without the
        # right cookie, transient parse anomaly, deleted-but-200): treat it like
        # a failed fetch — return None so get_download_url emits the [pid] error
        # sentinel and the PID stays pending, instead of writing a junk url_meta
        # entry that the batch flush would stamp meta_updated_at on. (B6)
        if not img_url or str(img_url) in ("None", ""):
            return None
        fallback_req = self._step3_safe_cookie_requirement(pid_key)
        need_cookie = self._refresh_cookie_requirement(pid_key, fallback=fallback_req)
        return (tag, like, pagecount, img_url, need_cookie,
                upload_date, create_date, user_id, user_name)

    @staticmethod
    def _step3_cache_is_usable(cached):
        """A cache entry counts as usable when img_url is non-empty AND pagecount > 0."""
        if not isinstance(cached, dict):
            return False
        if cached.get('img_url') in (None, 'None', ''):
            return False
        try:
            return int(cached.get('pagecount', 0) or 0) > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _coerce_rescrape_days(raw):
        """Coerce settings.json's ``rescrape_within_days`` into a non-negative int.

        Negative / non-numeric / None all collapse to 0 (feature disabled).
        """
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return 0
        return n if n > 0 else 0

    @staticmethod
    def _parse_pixiv_upload_date(value):
        """Parse Pixiv's ISO-8601 uploadDate string into a tz-aware datetime.

        Returns None for non-string / empty / unparseable input. Pixiv emits
        e.g. ``'2024-01-15T12:30:00+09:00'``. ``fromisoformat`` accepts that
        on Python >= 3.7, but rejects some valid ISO-8601 variants on Python
        3.10 (e.g. ``Z`` suffix, ``+0900`` without colon). The strptime
        fallback covers those cases without depending on a 3.11+ runtime.
        Naive inputs are treated as UTC defensively.
        """
        if not isinstance(value, str) or not value.strip():
            return None
        s = value.strip()
        try:
            dt = datetime.datetime.fromisoformat(s)
        except ValueError:
            dt = None
        if dt is None:
            # Normalize trailing 'Z' (Zulu / UTC) and tolerate offsets without
            # the colon, then retry via strptime.
            normalized = s
            if normalized.endswith('Z'):
                normalized = normalized[:-1] + '+0000'
            for fmt in (
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S",
            ):
                try:
                    dt = datetime.datetime.strptime(normalized, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt

    def _is_within_rescrape_window(self, cached):
        """True iff the cached entry was uploaded within ``self._rescrape_within_days`` days.

        Returns False (no rescrape) when:
          - the feature is disabled (threshold <= 0)
          - ``cached`` is not a dict
          - ``upload_date`` is missing or unparseable
          - the artwork is older than the threshold
          - the parsed upload_date is in the future (clock skew safeguard)
        """
        threshold = getattr(self, '_rescrape_within_days', 0)
        if threshold <= 0:
            return False
        if not isinstance(cached, dict):
            return False
        dt = self._parse_pixiv_upload_date(cached.get('upload_date'))
        if dt is None:
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        diff_days = (now - dt).total_seconds() / 86400.0
        return 0.0 <= diff_days < float(threshold)

    def _step3_cache_is_fresh(self, cached):
        """Cache short-circuits the network fetch only when both conditions hold:
           - the entry is well-formed (``_step3_cache_is_usable``); and
           - the artwork is NOT within the rescrape window.
        """
        if not self._step3_cache_is_usable(cached):
            return False
        if self._is_within_rescrape_window(cached):
            return False
        return True

    def _step3_finish_pid(self, ret_value, query_source, need_cookie, should_wait, pid_key):
        """Apply the trailing wait + progress advance, then format the return tuple."""
        if should_wait:
            self._sleep_ultra_slow(pid_key, need_cookie=need_cookie)
        self._step3_advance_progress()
        return self._step3_finalize_query(ret_value, query_source, need_cookie, bool(should_wait))

    def _resolve_meta_for_pid(self, pid_key, url, Agent, session, cookie_override):
        """Pick cache vs network meta. Returns ``(meta_tuple, query_source, should_wait)``
        where meta_tuple is either ``(tag, like, pagecount, img_url, need_cookie)`` or
        ``None`` to signal the network path failed and the caller should bail with [pid_key]."""
        cached = self._get_meta(pid_key) or None
        if self._step3_cache_is_fresh(cached):
            return (
                self._step3_resolve_meta_from_cache(pid_key, cached),
                "cache",
                False,
            )
        if self._step3_cache_is_usable(cached) and self._is_within_rescrape_window(cached):
            self._step3_safe_emit(
                f"<p><font color='gray'>PID {pid_key} 距上傳 &lt; "
                f"{self._rescrape_within_days} 天，重新抓取以更新讚數</font></p>"
            )
        resolved = self._step3_resolve_meta_from_network(
            pid_key, url, Agent, session, cookie_override
        )
        return resolved, "network", True

    def get_download_url(self, path, Agent, num, pid, *, cookie_override=None, session=None):
        """Resolve one PID into its download URL list, using cache → network."""
        self._pause_event.wait()
        if self._stop_event.is_set():
            return []
        pid_key = normalize_pid(pid)
        if not pid_key:
            return []

        if pid_key in self.exist_pid:
            self._step3_safe_emit(
                f"<p><font color='gray'>PID {pid_key} 已存在於 exist_pid，URL階段自動跳過</font></p>"
            )
            self._step3_advance_progress()
            return self._step3_finalize_query([], "skip", None, False)

        url = 'https://www.pixiv.net/artworks/' + pid_key
        meta_tuple, query_source, should_wait = self._resolve_meta_for_pid(
            pid_key, url, Agent, session, cookie_override,
        )
        if meta_tuple is None:
            return self._step3_finish_pid(
                [str(pid_key)], query_source, None, should_wait, pid_key,
            )
        (tag, like, pagecount, img_url, need_cookie,
         upload_date, create_date, user_id, user_name) = meta_tuple

        self._step3_build_url_meta_entry(
            pid_key, tag, like, pagecount, img_url, need_cookie, url, query_source,
            upload_date=upload_date, create_date=create_date,
            user_id=user_id, user_name=user_name,
        )

        passed, reason = self._passes_artwork_filters(pid_key, tag, like)
        self._step3_record_filter_result(pid_key, passed, reason)
        if not passed:
            return self._step3_finish_pid([], query_source, need_cookie, should_wait, pid_key)

        if not img_url or str(img_url) == 'None':
            return self._step3_finish_pid(
                [str(pid_key)], query_source, need_cookie, should_wait, pid_key
            )
        built_urls = self._step3_build_pid_download_urls(img_url, pagecount)
        if built_urls is None:
            return self._step3_finish_pid(
                [str(pid_key)], query_source, need_cookie, should_wait, pid_key
            )
        return self._step3_finish_pid(
            list(built_urls), query_source, need_cookie, should_wait, pid_key
        )

