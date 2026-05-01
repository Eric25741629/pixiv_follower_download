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
from queue import Queue
from PIL import Image
from pixiv_api import *
from app.core.worker_event import WorkerEvent
import tag_edit
import pixiv_api
from app.core.pixiv_thread_utils import (
    append_diagnostic_event,
    atomic_write_json,
    atomic_write_text,
    fetch_with_cookie_retry,
    init_cookie_fields,
    load_exist_pid_set,
    normalize_pid,
    output_err,
    safe_read_json,
    trash_file,
)
from app.core.pixiv_thread_base import (
    PauseableThread,
    _normalize_special_like_rules,
    _resolve_like_threshold,
    _is_ai_artwork_tagged,
    _cookie_usage_label,
    _format_cookie_usage_summary,
)

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
        single_thread_mode=False,
        intra_pid_wait_min=1,
        intra_pid_wait_max=3,
        jxl_enable=False,
        jxl_cjxl_path=r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe",
        jxl_delete_original=False,
        jxl_effort=7,
        scheduler=None,
        stats_collector=None,
        *legacy_args,
        **legacy_kwargs,
    ):
        super().__init__(q, scheduler=scheduler)
        self.nogif=nogif
        self.notime=notime
        self.notag=notag
        self.create_dir=create_dir
        self.download_path=download_path     
        self.cookie_entries, self.cookie_pool, self._cookie_alias_map, self.cookies = init_cookie_fields(cookies)
        self._pid_cookie_selection = {}
        self._current_account = None  # set by _execute_downloads when scheduler is active
        self.agent=agent
        self.download_time=download_time
        self.no_R18G_dir=no_R18G_dir
        self.single_thread_mode = single_thread_mode
        self.ai_gen_dir = False
        self._stats_collector = stats_collector
        # explicit local flag for clarity elsewhere in code
        self.single_mode_flag = bool(single_thread_mode)
        try:
            self.intra_pid_wait_min = int(intra_pid_wait_min)
            self.intra_pid_wait_max = int(intra_pid_wait_max)
        except Exception:
            self.intra_pid_wait_min, self.intra_pid_wait_max = 1, 3
        if self.intra_pid_wait_min < 0:
            self.intra_pid_wait_min = 0
        if self.intra_pid_wait_max < self.intra_pid_wait_min:
            self.intra_pid_wait_max = self.intra_pid_wait_min
        # Legacy inter-PID cooldown (used only when no scheduler is injected).
        # Read once at construction; live reloads happen via the scheduler path.
        try:
            from app.core.settings_store import SettingsStore as _SS
            self._legacy_pid_cooldown_avg = int(
                _SS(os.getenv("APPDATA") + r"/pixiv_download/")
                .get_section("performance").get("pid_cooldown_avg", 35)
            )
        except Exception:
            self._legacy_pid_cooldown_avg = 35
        # Backward compatibility: accept older positional/keyword constructor calls.
        overrides = self._apply_legacy_constructor_args(legacy_args, legacy_kwargs)
        jxl_enable = overrides.get("jxl_enable", jxl_enable)
        jxl_cjxl_path = overrides.get("jxl_cjxl_path", jxl_cjxl_path)
        jxl_delete_original = overrides.get("jxl_delete_original", jxl_delete_original)
        jxl_effort = overrides.get("jxl_effort", jxl_effort)
        like_num = overrides.get("like_num", 0)
        ban_tag = overrides.get("ban_tag", [])
        must_tag = overrides.get("must_tag", [])
        special_like_rules = overrides.get("special_like_rules", [])
        self.ai_gen_dir = overrides.get("ai_gen_dir", self.ai_gen_dir)
        self.jxl_enable = bool(jxl_enable)
        jxl_path_raw = str(jxl_cjxl_path).strip() if str(jxl_cjxl_path).strip() else r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe"
        self.jxl_cjxl_path = self._resolve_cjxl_path(jxl_path_raw)
        self.jxl_delete_original = bool(jxl_delete_original)
        try:
            self.jxl_effort = int(jxl_effort)
        except Exception:
            self.jxl_effort = 7
        if self.jxl_effort < 1:
            self.jxl_effort = 1
        if self.jxl_effort > 9:
            self.jxl_effort = 9
        self._jxl_path_warned = False
        self._jxl_gif_skip_warned = False
        self._jxl_ok_count = 0
        self._jxl_fail_count = 0
        self._jxl_src_total_bytes = 0
        self._jxl_dst_total_bytes = 0
        self.like_num = like_num if like_num > 0 else 0
        self.ban_tag = ban_tag
        self.must_tag = must_tag
        self.special_like_rules = special_like_rules
        self._ban_tag_norm = self._normalize_filter_tags(self.ban_tag)
        self._must_tag_norm = self._normalize_filter_tags(self.must_tag)
        self._pid_filter_decision = {}
        self.url_meta = {}
        self.url_meta_path = os.path.join(self.path, "all_url_meta.json")
        self.allurl = []
        self.exist_json_path = os.path.join(self.path, "exist_pid.json")
        self.legacy_exist_json_path = os.path.join(self.path, "exist.json")
        self.exist_txt_path = os.path.join(self.path, "existPID.txt")
        
        self.q=Queue()
        self._stop_after_group = False
        self._stopped_by_request = False
        self._active_group_pid = None
        self._attempted_urls = set()
        self._attempted_urls_lock = threading.Lock()
        self._pid_cookie_used = {}
        self._task_filter_stats = {}
        self._step4_filter_skip_counts = {"tag": 0, "like": 0, "no_meta": 0}
        self._step4_filter_skip_notice_emitted = False
        self._step4_filter_skip_every = 200
        self._cookie_usage_counts = {"step4": {}}
        self._cookie_usage_seen = {"step4": set()}
        if not os.path.exists(self.download_path):
            os.mkdir(self.download_path)
        try:
            base_exist = load_exist_pid_set(self.path)
            self.exist_pid = base_exist.union(set(self.splitID(self.get_filelist(self.download_path))))
        except Exception:
            self.exist_pid = set(self.splitID(self.get_filelist(self.download_path)))

        from app.core.pixiv_thread_utils import read_json_with_recovery
        meta, meta_status = read_json_with_recovery(
            self.url_meta_path, default={},
            emit=lambda html: self._q.put(WorkerEvent("output", html)),
        )
        self.url_meta = meta if isinstance(meta, dict) else {}
        # Surface a clear warning when meta is missing/empty AND a like
        # filter is configured — otherwise step 4 silently flags every
        # PID as "no_meta" and the user sees an empty download phase.
        if (
            not self.url_meta
            and (int(self.like_num or 0) > 0 or bool(self.special_like_rules))
        ):
            try:
                self._q.put(WorkerEvent("output",
                    "<p><font color='red'>[警告] all_url_meta.json 為空，"
                    "且設定了愛心過濾門檻；步驟 4 將嘗試即時補抓 meta，"
                    "若仍失敗會被全部標為「無meta」跳過。建議先重跑步驟 3。</font></p>"
                ))
            except Exception:
                pass

        try:
            print("正在讀取 all_url.txt...",self.path+r"/all_url.txt")
            with open(self.path+r"/all_url.txt") as file:    
                self.allurl = [line.rstrip() for line in file if line.rstrip()]

            print(f"all_url.txt 讀取完成，URL數量：{len(self.allurl)}")
            print("正在過濾已存在的 PID...")
        except Exception:
            self._q.put(WorkerEvent("finished", 'Task finished'))
        raw_allurl_count = len(self.allurl)
        self.allurl, self._task_filter_stats = self._prepare_download_tasks(self.allurl)
        self.pid_max=len(self.allurl)
        self._diag(
            "step4_init",
            raw_all_url_count=raw_allurl_count,
            filtered_all_url_count=self.pid_max,
            exist_pid_count=len(self.exist_pid),
            url_meta_count=len(self.url_meta),
            like_min=int(self.like_num or 0),
            special_like_rule_count=len(self.special_like_rules),
            ai_gen_dir=bool(self.ai_gen_dir),
            ban_tag_count=len(self._ban_tag_norm),
            must_tag_count=len(self._must_tag_norm),
            task_filter_stats=self._task_filter_stats if isinstance(self._task_filter_stats, dict) else {},
            single_mode=bool(self.single_mode_flag),
        )
        print(self.pid_max)   

    def _record_cookie_usage(self, stage, pid, cookie_value):
        stage_key = str(stage or "").strip().lower()
        if stage_key not in self._cookie_usage_counts:
            return ""
        cookie_text = str(cookie_value or "").strip()
        label = _cookie_usage_label(cookie_text, self.cookie_pool, self._cookie_alias_map)
        if not cookie_text:
            return label
        pid_key = normalize_pid(pid) or str(pid)
        try:
            seen = self._cookie_usage_seen.setdefault(stage_key, set())
            if pid_key in seen:
                return label
            seen.add(pid_key)
            counts = self._cookie_usage_counts.setdefault(stage_key, {})
            counts[label] = int(counts.get(label, 0)) + 1
        except Exception:
            pass
        return label

    def _emit_cookie_usage_summary(self, stage, title):
        try:
            stage_key = str(stage or "").strip().lower()
            counts = self._cookie_usage_counts.get(stage_key, {}) if isinstance(self._cookie_usage_counts, dict) else {}
            summary = _format_cookie_usage_summary(counts, self.cookie_pool, self._cookie_alias_map)
            self._q.put(WorkerEvent("output", f"<p><font color='gray'>[{title}] {summary}</font></p>"))
        except Exception:
            pass

    def _diag(self, event, **fields):
        try:
            append_diagnostic_event(self.path, event, stage="step4", **fields)
        except Exception:
            pass

    def _count_text_lines(self, file_path):
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                return sum(1 for line in f if str(line).strip())
        except Exception:
            return 0

    def _write_all_url_file(self, urls, reason="unknown"):
        file_path = os.path.join(self.path, "all_url.txt")
        safe_lines = [str(x) for x in (urls or [])]
        before_count = self._count_text_lines(file_path)
        self._diag(
            "all_url_write_attempt",
            reason=reason,
            before_count=before_count,
            target_count=len(safe_lines),
        )
        try:
            atomic_write_text(file_path, safe_lines, backup=True)
            after_count = self._count_text_lines(file_path)
            self._diag(
                "all_url_write_done",
                reason=reason,
                success=True,
                before_count=before_count,
                after_count=after_count,
            )
            return True
        except Exception as err:
            self._diag(
                "all_url_write_primary_failed",
                reason=reason,
                error=str(err),
            )
            try:
                from safe_io import backup_file
                backup_file(file_path)
            except Exception:
                pass
            try:
                with open(file_path, "w+", encoding="utf-8") as f:
                    f.writelines([line + "\n" for line in safe_lines])
                after_count = self._count_text_lines(file_path)
                self._diag(
                    "all_url_write_done",
                    reason=reason,
                    success=True,
                    via="fallback",
                    before_count=before_count,
                    after_count=after_count,
                )
                return True
            except Exception as fallback_err:
                self._diag(
                    "all_url_write_done",
                    reason=reason,
                    success=False,
                    error=str(fallback_err),
                )
                return False

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
        try:
            if isinstance(value, str):
                s = value.strip().replace(",", "").replace("_", "")
                if not s:
                    return default
                return int(float(s))
            return int(value)
        except Exception:
            return default

    def _normalize_filter_tags(self, tags):
        out = []
        if not isinstance(tags, list):
            return out
        try:
            tags = tag_edit.Tag(tags)
        except Exception:
            pass
        for t in tags:
            s = str(t).strip()
            if s:
                out.append(s.lower())
        return list(dict.fromkeys(out))

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

    def _is_r18_artwork(self, tag):
        artwork_tags = self._normalize_artwork_tags(tag)
        for marker in ("r-18g", "r-18", "糞", "子宮脫"):
            if self._tag_hit(marker, artwork_tags):
                return True
        return False

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
        if (not self.no_R18G_dir) and self._is_r18_artwork(tag):
            base_dir = os.path.join(base_dir, "R-18G")
        os.makedirs(base_dir, exist_ok=True)
        return base_dir

    def _record_step4_filter_skip(self, reason, pid_key=None):
        key = str(reason or "other")
        try:
            if key not in self._step4_filter_skip_counts:
                self._step4_filter_skip_counts[key] = 0
            self._step4_filter_skip_counts[key] += 1
        except Exception:
            pass
        try:
            self._diag("step4_filter_skip", reason=key, pid=str(pid_key or ""))
        except Exception:
            pass
        total = 0
        try:
            total = int(sum(int(v or 0) for v in self._step4_filter_skip_counts.values()))
        except Exception:
            total = 0
        try:
            if not self._step4_filter_skip_notice_emitted:
                self._step4_filter_skip_notice_emitted = True
                self._q.put(WorkerEvent("output", "<p><font color='gray'>[Step4過濾] 已啟用精簡輸出，將改為摘要顯示</font></p>"))
            if total > 0 and total % int(self._step4_filter_skip_every) == 0:
                self._q.put(WorkerEvent("output",
                    "<p><font color='gray'>[Step4過濾摘要] 已略過 {} 筆（標籤={}、低愛心={}、無meta={}）</font></p>".format(
                        total,
                        int(self._step4_filter_skip_counts.get("tag", 0)),
                        int(self._step4_filter_skip_counts.get("like", 0)),
                        int(self._step4_filter_skip_counts.get("no_meta", 0)),
                    )
                ))

        except Exception:
            pass

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

    def _select_cookie_for_pid(self, pid):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            if pid_key in self._pid_cookie_selection:
                return self._pid_cookie_selection.get(pid_key, "")
        except Exception:
            pass
        if self.cookie_pool:
            selected = pyrandom.choice(self.cookie_pool)
        else:
            selected = str(self.cookies or "").strip()
        try:
            self._pid_cookie_selection[pid_key] = selected
        except Exception:
            pass
        return selected

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
            meta = self.url_meta.get(str(pid), {}) if isinstance(self.url_meta, dict) else {}
            if isinstance(meta, dict) and meta:
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
        meta = self.url_meta.get(str(pid), {}) if isinstance(self.url_meta, dict) else {}
        if isinstance(meta, dict) and meta and 'tag' in meta:
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

    def _fetch_meta_for_filter(self, pid, allow_network=False):
        pid_key = normalize_pid(pid)
        if not pid_key:
            return None
        meta = self.url_meta.get(pid_key) if isinstance(self.url_meta, dict) else None
        if isinstance(meta, dict) and (meta.get("tag") is not None or meta.get("like") is not None):
            return meta
        if not allow_network:
            return None
        need_cookie = self._refresh_cookie_requirement(pid_key, fallback=None)
        url = "https://www.pixiv.net/artworks/" + pid_key
        # Route the network fallback through the scheduler when present so
        # the bound proxy + per-account cooldown applies (otherwise this
        # path bypasses proxies and hammers pixiv unthrottled — every PID
        # rate-limits and then the whole step 4 ends up "no_meta").
        info = None
        if self._scheduler is not None:
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

            try:
                ok, info, _ = self._run_with_network_retry(
                    f"PID {pid_key}", _do_fetch,
                )
            except Exception:
                ok = True
                info = None
            self._release_account(acc, ok=ok)
        else:
            pid_cookie = self._select_cookie_for_pid(pid_key)
            self._record_cookie_usage("step3", pid_key, pid_cookie)
            try:
                if need_cookie is False:
                    info = pixiv_api.Pixiv_info(url, self.agent)
                elif pid_cookie:
                    info = pixiv_api.Pixiv_info(url, self.agent, cookie=pid_cookie)
                else:
                    info = pixiv_api.Pixiv_info(url, self.agent)
            except Exception:
                info = None
        normalized = self._normalize_pixiv_info(info)
        if not normalized:
            return None
        tag, like, pagecount, img_url = normalized
        meta = {
            "tag": tag if isinstance(tag, list) else [],
            "like": self._to_int(like, like),
            "pagecount": self._to_int(pagecount, pagecount),
            "img_url": img_url,
            "requires_cookie": need_cookie,
            "artwork_url": url,
            "pixiv_info": {
                "tag": tag if isinstance(tag, list) else [],
                "like": self._to_int(like, like),
                "pagecount": self._to_int(pagecount, pagecount),
                "img_url": img_url,
                "requires_cookie": need_cookie,
                "queried_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "filter_fetch",
            },
        }
        try:
            self.url_meta[pid_key] = meta
        except Exception:
            pass
        return meta

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
            if self.like_num > 0 or self.special_like_rules:
                self._record_step4_filter_skip("no_meta", pid_key=pid_key)
                decision = (False, "no_meta")
            else:
                # No like threshold configured; keep pending.
                decision = (True, "no_meta")
            self._pid_filter_decision[pid_key] = decision
            return decision

        artwork_tags = self._normalize_artwork_tags(meta.get("tag", []))
        for blocked in self._ban_tag_norm:
            if self._tag_hit(blocked, artwork_tags):
                self._record_step4_filter_skip("tag", pid_key=pid_key)
                decision = (False, "tag")
                self._pid_filter_decision[pid_key] = decision
                return decision

        if self._must_tag_norm:
            matched = False
            for required in self._must_tag_norm:
                if self._tag_hit(required, artwork_tags):
                    matched = True
                    break
            if not matched:
                self._record_step4_filter_skip("tag", pid_key=pid_key)
                decision = (False, "tag")
                self._pid_filter_decision[pid_key] = decision
                return decision

        like_value = self._to_int(meta.get("like"), None)
        like_limit, _matched_rules = _resolve_like_threshold(
            self.like_num,
            artwork_tags,
            self.special_like_rules,
            self._tag_hit,
            self._to_int,
        )
        if like_limit > 0 and like_value is not None and like_value < like_limit:
            self._record_step4_filter_skip("like", pid_key=pid_key)
            decision = (False, "like")
            self._pid_filter_decision[pid_key] = decision
            return decision

        decision = (True, "pass")
        self._pid_filter_decision[pid_key] = decision
        return decision

    @staticmethod
    def _apply_legacy_constructor_args(legacy_args, legacy_kwargs):
        """Resolve backward-compatible positional/keyword args to a dict of overrides.

        Silently skips malformed entries so a caller passing junk cannot break __init__.
        """
        overrides = {}

        def _set(key, caster, value):
            try:
                overrides[key] = caster(value)
            except Exception:
                pass

        positional_schema = [
            ("jxl_enable", bool),
            ("jxl_cjxl_path", lambda v: str(v).strip() or None),
            ("jxl_delete_original", bool),
            ("jxl_effort", int),
        ]
        for idx, (key, caster) in enumerate(positional_schema):
            if idx >= len(legacy_args or ()):
                break
            casted_value = None
            try:
                casted_value = caster(legacy_args[idx])
            except Exception:
                continue
            if casted_value is None:
                continue
            overrides[key] = casted_value

        kwargs = legacy_kwargs or {}
        scalar_schema = [
            ("jxl_enable", bool),
            ("jxl_cjxl_path", lambda v: str(v).strip() or None),
            ("jxl_delete_original", bool),
            ("jxl_effort", int),
            ("like_num", lambda v: int(v or 0)),
            ("ai_gen_dir", bool),
        ]
        for key, caster in scalar_schema:
            if key not in kwargs:
                continue
            try:
                casted_value = caster(kwargs[key])
            except Exception:
                continue
            if casted_value is None:
                continue
            overrides[key] = casted_value

        for list_key in ("ban_tag", "must_tag"):
            value = kwargs.get(list_key)
            if isinstance(value, list):
                overrides[list_key] = value

        if "special_like_rules" in kwargs:
            try:
                overrides["special_like_rules"] = _normalize_special_like_rules(
                    kwargs.get("special_like_rules", [])
                )
            except Exception:
                pass

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
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return False, None, None
        if not os.path.isfile(src_path):
            return False, None, None
        if not os.path.isfile(self.jxl_cjxl_path):
            if not self._jxl_path_warned:
                self._jxl_path_warned = True
                try:
                    self._q.put(WorkerEvent("output",
                        f"<p><font color='orange'>JXL 已啟用，但找不到 cjxl：{self.jxl_cjxl_path}</font></p>"
                    ))
                except Exception:
                    pass
            return False, None, None
        dst_path = os.path.splitext(src_path)[0] + ".jxl"
        if os.path.isfile(dst_path):
            try:
                self._jxl_ok_count += 1
                if self.jxl_delete_original and os.path.isfile(src_path):
                    os.remove(src_path)
            except Exception:
                pass
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

    def _jxl_record_outcome(self, src_path, dst_path, ok, reason):
        """Update counters, emit log lines and optionally delete the source."""
        if ok and os.path.isfile(dst_path):
            self._jxl_ok_count += 1
            saved_bytes = None
            src_size = None
            dst_size = None
            try:
                src_size = os.path.getsize(src_path)
                dst_size = os.path.getsize(dst_path)
                if src_size >= 0 and dst_size >= 0:
                    self._jxl_src_total_bytes += src_size
                    self._jxl_dst_total_bytes += dst_size
                    saved_bytes = src_size - dst_size
                    if self._stats_collector is not None:
                        self._stats_collector.report_jxl(src_size, dst_size)
            except Exception:
                pass
            try:
                if saved_bytes is not None and src_size and src_size > 0:
                    saved_ratio = (saved_bytes / float(src_size)) * 100.0
                    self._q.put(WorkerEvent("output",
                        f"<p><font color='gray'>JXL 對比：{os.path.basename(src_path)} {self._format_size_human(src_size)} → {self._format_size_human(dst_size)}（省下 {self._format_size_human(saved_bytes)}, {saved_ratio:.2f}%）</font></p>"
                    ))
            except Exception:
                pass
            if self.jxl_delete_original:
                try:
                    os.remove(src_path)
                except Exception:
                    pass
            return
        self._jxl_fail_count += 1
        if len(reason) > 120:
            reason = reason[:117] + "..."
        if not reason:
            reason = "cjxl conversion failed"
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='orange'>JXL 轉檔失敗：{os.path.basename(src_path)} ({reason})</font></p>"
            ))
        except Exception:
            pass

    def _convert_file_to_jxl(self, src_path):
        proceed, dst_path, ext = self._jxl_should_convert(src_path)
        if not proceed:
            return
        src_path = str(src_path)
        ok, reason = self._jxl_run_conversion(src_path, dst_path, ext)
        self._jxl_record_outcome(src_path, dst_path, ok, reason)

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
    def _build_download_filename(pid, *, page_suffix, ext, hashtag, timetag, notag, notime):
        """Compose a download filename.

        Layout: "[timetag_]PID{pid}{page_suffix}[{hashtag}].{ext}"
        - timetag prefix added only when notime is False.
        - hashtag suffix added only when notag is False (hashtag carries its own
          leading spaces by convention — see _build_hashtag_text).
        - the underscore between timetag and "PID..." is added only when both parts exist.
        """
        parts = []
        if not notime and timetag:
            parts.append(timetag)
        core = 'PID' + str(pid) + (page_suffix or '')
        if not notag:
            core += hashtag
        parts.append(core)
        return '_'.join(parts) + '.' + ext

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

    def _run_download_countdown(self, pid, min_sec, max_sec, *, label, color, respect_group_stop):
        if not self.single_mode_flag:
            return
        delay = self._calc_sleep_delay(min_sec, max_sec, pid=pid)
        cookie_used = self._is_cookie_used_for_pid(pid)
        ratio_text = '1.0x' if cookie_used else '0.5x'
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='{color}'>[下載等待][{label}] 等待 {delay} 秒 (PID {pid}, 倍率 {ratio_text}, cookie_used={cookie_used})</font></p>"
            ))
        except Exception:
            pass
        for remaining in range(int(delay), 0, -1):
            if self._stop_event.is_set():
                break
            if respect_group_stop and self._stop_after_group:
                break
            while not self._pause_event.is_set():
                if self._stop_event.is_set():
                    break
                self._pause_event.wait(timeout=0.5)
            if self._stop_event.is_set():
                break
            try:
                self._q.put(WorkerEvent("countdown", remaining))
            except Exception:
                pass
            if self._stop_event.wait(timeout=1.0):
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
            meta = self.url_meta.get(pid_key, {}) if isinstance(self.url_meta, dict) else {}
            req = meta.get('requires_cookie', None) if isinstance(meta, dict) else None
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
            meta = self.url_meta.get(pid_key, {}) if isinstance(self.url_meta, dict) else {}
            if not isinstance(meta, dict) or not meta:
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
            meta = self.url_meta.get(pid, {}) if isinstance(self.url_meta, dict) else {}
            img_url = str(meta.get("img_url", "")).strip() if isinstance(meta, dict) else ""
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

    def _prepare_download_tasks(self, urls, allow_network=False):
        pending = []
        seen_url = set()
        no_meta_pids = set()  # PIDs that fell through filter for lack of meta
        stats = {
            "input_count": 0,
            "duplicate_count": 0,
            "invalid_count": 0,
            "skipped_exist_count": 0,
            "skipped_like_count": 0,
            "skipped_tag_count": 0,
            "skipped_no_meta_count": 0,
            "output_count": 0,
        }
        for raw in urls:
            stats["input_count"] += 1
            if not isinstance(raw, str):
                stats["invalid_count"] += 1
                continue
            u = raw.strip()
            if not u:
                stats["invalid_count"] += 1
                continue
            if u in seen_url:
                stats["duplicate_count"] += 1
                continue
            seen_url.add(u)
            pid = normalize_pid(self._extract_pid_from_download_url(u))
            if not pid:
                stats["invalid_count"] += 1
                continue
            if pid in self.exist_pid:
                stats["skipped_exist_count"] += 1
                continue
            passed, reason = self._passes_pid_filter(pid, allow_network=allow_network)
            if not passed:
                if reason == "like":
                    stats["skipped_like_count"] += 1
                elif reason == "tag":
                    stats["skipped_tag_count"] += 1
                elif reason == "no_meta":
                    stats["skipped_no_meta_count"] += 1
                    no_meta_pids.add(pid)
                else:
                    stats["invalid_count"] += 1
                continue
            pending.append(u)
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

    def _requeue_no_meta_pids(self, pids: set) -> None:
        """Append PIDs that step 4 couldn't resolve meta for back into
        pictures_id.txt (step 3's pending queue) so the user's next step
        3 run picks them up. Merges with whatever is already there."""
        try:
            pending_path = os.path.join(self.path, "pictures_id.txt")
            existing = set()
            if os.path.isfile(pending_path):
                try:
                    with open(pending_path, encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            s = line.strip()
                            if s:
                                existing.add(s)
                except Exception:
                    pass
            new = {str(p).strip() for p in pids if str(p).strip()}
            merged = existing | new
            added = len(new - existing)
            if added <= 0:
                return
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
        has_actual_download = False  # 是否真的有成功下載任何檔案
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
                else:
                    # 至少成功下載一張
                    has_actual_download = True
                    # 同一 PID 多頁時，頁面間做短暫休眠
                    if idx < len(urls) - 1:
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
        try:
            self._q.put(WorkerEvent("output", "<p><font color='orange'>Step4 無待下載 URL，保留現有 all_url.txt 不改寫</font></p>"))
        except Exception:
            pass
        if self._stopped_by_request or self._stop_event.is_set():
            self._q.put(WorkerEvent("finished", 'Task finished'))
            self._q.put(WorkerEvent("next", -1))
        else:
            self._q.put(WorkerEvent("finished", '下載完成'))

    def _execute_downloads(self, pid_order, pid_groups):
        failed_nested = []
        if self.single_mode_flag:
            self._q.put(WorkerEvent("output", "<p><font color='green'>下載模式：單執行緒 + 每個 PID 共用單一 Session</font></p>"))
            if self._scheduler is not None:
                inter_pid_desc = "由排程器管理（單帳號平均冷卻）"
            else:
                inter_pid_desc = "固定冷卻（pid_cooldown_avg）"
            self._q.put(WorkerEvent("output",
                f"<p><font color='green'>同PID等待: {self.intra_pid_wait_min}~{self.intra_pid_wait_max} 秒；PID間: {inter_pid_desc}</font></p>"))
            for idx, pid in enumerate(pid_order, start=1):
                if self._stop_after_group:
                    try:
                        self._q.put(WorkerEvent("output", "<p><font color='orange'>收到中止要求：已在上一個 PID 組完成後停止</font></p>"))
                    except Exception:
                        pass
                    break
                if self._stop_event.is_set():
                    break
                self._active_group_pid = pid
                self._q.put(WorkerEvent("output", f"<p><font color='black'>處理 PID {idx}/{len(pid_order)}：{pid}（{len(pid_groups.get(pid, []))} 張）</font></p>"))

                if self._scheduler is not None:
                    acc = self._acquire_account()
                    if acc is None:
                        break  # stop signal or all disabled
                    # Sticky cookie: this PID's pages all use this cookie+proxy
                    pid_key = normalize_pid(pid) or str(pid)
                    self._pid_cookie_selection[pid_key] = acc.cookie
                    self._current_account = acc
                    ok = True
                    result = []
                    try:
                        result = self._download_pid_group(pid, pid_groups.get(pid, []))
                    except (requests.exceptions.ProxyError,
                            requests.exceptions.ConnectTimeout,
                            requests.exceptions.ConnectionError) as err:
                        ok = False
                        try:
                            self._q.put(WorkerEvent("output",
                                f"<p><font color='red'>PID {pid} 因 proxy 失敗略過：{err.__class__.__name__}</font></p>"))
                        except Exception:
                            pass
                    finally:
                        self._current_account = None
                        self._release_account(acc, ok=ok)
                    failed_nested.append(result if isinstance(result, list) else [])
                else:
                    failed_nested.append(self._download_pid_group(pid, pid_groups.get(pid, [])))

                self._active_group_pid = None
                if self._stop_event.is_set():
                    break
                # _sleep_between_downloads is a no-op when scheduler is set;
                # call unconditionally — the method itself decides whether to sleep.
                if idx < len(pid_order):
                    self._sleep_between_downloads(pid)
        else:
            self._q.put(WorkerEvent("output", "<p><font color='gray'>下載模式：多執行緒（以 PID 為單位分派；每個 PID 仍共用單一 Session）</font></p>"))
            max_workers = 4
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as self.executor:
                futures = [self.executor.submit(self._download_pid_group, pid, pid_groups.get(pid, [])) for pid in pid_order]
                for fu in concurrent.futures.as_completed(futures):
                    try:
                        failed_nested.append(fu.result())
                    except Exception:
                        failed_nested.append([])
        return failed_nested

    @staticmethod
    def _classify_download_results(failed_nested):
        """Flatten nested worker results into ``(url_text, info_text)`` fail records.

        Filters out ``0`` sentinels and ``None`` entries; tolerates list/tuple/str
        shapes returned by the per-PID workers.
        """
        results = [i for item in failed_nested if isinstance(item, list) for i in item if i != 0]
        fail_records = []
        for item in results:
            if isinstance(item, (list, tuple)):
                if len(item) >= 2:
                    fail_records.append((str(item[0]), str(item[1])))
                elif len(item) == 1:
                    fail_records.append((str(item[0]), ""))
            elif isinstance(item, str):
                fail_records.append((item, ""))
            elif item is None:
                continue
            else:
                fail_records.append((str(item), ""))
        return fail_records

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

    def _persist_url_meta(self):
        """Best-effort save of ``self.url_meta`` (atomic with backup, raw fallback)."""
        try:
            try:
                from safe_io import atomic_write_json
                atomic_write_json(self.url_meta_path, self.url_meta, backup=True)
            except Exception:
                with open(self.url_meta_path, 'w', encoding='utf-8') as f:
                    json.dump(self.url_meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _finalize_downloads(self, failed_nested):
        fail_records = self._classify_download_results(failed_nested)
        if fail_records:
            err_lines = [f"{url_text} {info_text}" for url_text, info_text in fail_records]
            atomic_write_text(self.path + "/err_url.txt", err_lines, backup=False)
        self._diag("step4_fail_records", fail_record_count=len(fail_records))
        stop_to_download = [self.q.get() for _ in range(self.q.qsize())]
        remaining_urls = self._compute_remaining_urls(stop_to_download, fail_records)
        self._write_all_url_file(remaining_urls, reason="step4_remaining")
        try:
            self._q.put(WorkerEvent("output", f"<p><font color='gray'>已更新 all_url.txt，剩餘 {len(remaining_urls)} 筆待下載</font></p>"))
        except Exception:
            pass
        self._persist_url_meta()
        return remaining_urls

    def _refresh_and_write_exist_pid(self):
        # Merge disk state into memory (handles migration implicitly via load_exist_pid_set)
        disk_pids = load_exist_pid_set(self.path)
        self.exist_pid.update(disk_pids)
        download_id = self.splitID(self.get_filelist(self.download_path))
        self.exist_pid.update(download_id)
        # Write JSON only — no sorted(), no txt double-write
        try:
            atomic_write_json(self.exist_json_path, list(self.exist_pid), backup=True)
        except Exception:
            try:
                with open(self.exist_json_path, 'w', encoding='utf-8') as f:
                    json.dump(list(self.exist_pid), f, ensure_ascii=False)
            except Exception:
                pass
        # Trash any remaining legacy files
        for old in [self.legacy_exist_json_path, self.exist_txt_path]:
            if os.path.isfile(old):
                trash_file(old, self.path)

    def _emit_step4_summary_and_finalize(self, remaining_urls):
        if self.jxl_enable:
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
            self.allurl, self._task_filter_stats = self._prepare_download_tasks(self.allurl, allow_network=True)
            self.pid_max = len(self.allurl)
            self._diag(
                "step4_run_start",
                pending_url_count=self.pid_max,
                filter_stats=self._task_filter_stats if isinstance(self._task_filter_stats, dict) else {},
            )
            self._emit_step4_header()
            if self.pid_max <= 0:
                self._handle_zero_pending()
                return
            pid_order, pid_groups = self._group_urls_by_pid(self.allurl)
            self._diag("step4_grouped", pid_count=len(pid_order), url_count=len(self.allurl))
            self._q.put(WorkerEvent("output", f"<p><font color='gray'>PID 分組完成：{len(pid_order)} 個 PID、{len(self.allurl)} 個 URL</font></p>"))
            failed_nested = self._execute_downloads(pid_order, pid_groups)
            remaining_urls = self._finalize_downloads(failed_nested)
            self._refresh_and_write_exist_pid()
            self._emit_step4_summary_and_finalize(remaining_urls)
        except Exception as e:
            self._diag("step4_exception", error=output_err(e))
            self._q.put(WorkerEvent("output", 'Task failed'))
            self._q.put(WorkerEvent("output", output_err(e)))
            self._q.put(WorkerEvent("next", -1))
    def gif_or_jpg(self,url, session=None):
        original_url = str(url)
        resolved_url = self._resolve_download_url(original_url)
        try:
            pid_from_original = normalize_pid(self._extract_pid_from_download_url(original_url))
            pid_from_resolved = normalize_pid(self._extract_pid_from_download_url(resolved_url))
            pid_for_log = pid_from_original or pid_from_resolved or "unknown"
            page = self._extract_page_from_download_url(resolved_url)
            if page is None:
                page = self._extract_page_from_download_url(original_url)
            media = "ugoira" if "ugoira" in str(resolved_url).lower() else "image"
            if page is None:
                self._q.put(WorkerEvent("output", f"<p><font color='black'>[下載] PID {pid_for_log} ({media})</font></p>"))
            else:
                self._q.put(WorkerEvent("output", f"<p><font color='black'>[下載] PID {pid_for_log} 第 {int(page) + 1} 張 ({media})</font></p>"))
        except Exception:
            pass
        self._pause_event.wait()
        try:
            with self._attempted_urls_lock:
                self._attempted_urls.add(original_url)
        except Exception:
            pass
        if not self._stop_event.is_set():
            self.pid_now=self.pid_now+1
            self._q.put(WorkerEvent("progress", (1, self.pid_max)))
        if self._stop_event.is_set():
            self.q.put(original_url)
            return 0
        if 'ugoira' in resolved_url:
            pid = normalize_pid(resolved_url.rsplit('/', 1)[1].rsplit('_', 1)[0].rsplit('ugoira0')[0])
            if(pid in self.exist_pid):
                print('頝喲?')
                return -1  # 頝喲?璅??
            else:
                ret = self.gif_download(resolved_url, session=session)
                if isinstance(ret, list) and ret and str(ret[0]) == resolved_url:
                    ret[0] = original_url
                return ret
        else:
            pid = normalize_pid(resolved_url.rsplit('/', 1)[1].split('_', 1)[0])
            if(pid in self.exist_pid):
                print('頝喲?')
                return -1  # 頝喲?璅??
            else:
                ret = self.jpg_download(resolved_url, session=session)
                if isinstance(ret, list) and ret and str(ret[0]) == resolved_url:
                    ret[0] = original_url
                return ret
    def __del__(self):
        try:
            executor = getattr(self, 'executor', None)
            if executor is not None:
                executor.shutdown(wait=False)
        except Exception:
            pass
        try:
            self.wait()
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

        try:
            meta = self.url_meta.get(pid_key, {}) if isinstance(self.url_meta, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["requires_cookie"] = True
            meta["cookie_used"] = True
            meta["cookie_used_source"] = str(source)
            meta["cookie_used_updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.url_meta[pid_key] = meta
        except Exception:
            pass

        try:
            atomic_write_json(self.url_meta_path, self.url_meta, backup=True)
        except Exception:
            try:
                with open(self.url_meta_path, 'w', encoding='utf-8') as f:
                    json.dump(self.url_meta, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='blue'>[GIF][Cookie] PID {pid_key} 使用 cookies（來源：{source}），已更新 all_url_meta 暫存</font></p>"
            ))
        except Exception:
            pass

    def gif_download(self,url, session=None):
        my_time = self.download_time
        try:
            pid, pid_cookie, need_cookie = self._resolve_pid_and_cookie(url, source="step4")
            normalized = self._load_artwork_metadata(pid, pid_cookie)
            if not normalized:
                try:
                    self._q.put(WorkerEvent("output", f"<p><font color='orange'>PID {pid} 取得 ugoira 資訊失敗，已標記為失敗任務</font></p>"))
                except Exception:
                    pass
                return [url, my_time.strftime('%Y%m%d_%H%M%S')]
            tag,like,pagecount,img_url = normalized
            url='https://www.pixiv.net/ajax/illust/%s/ugoira_meta?lang=zh_tw'%pid
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
            try:
                if bool(meta_trace.get("retry_used")) and int(meta_trace.get("retry_with_cookie_status") or 0) == 200:
                    need_cookie = True
                    self._mark_gif_cookie_usage(pid, True, source="ugoira_meta_retry")
            except Exception:
                pass
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
            if htmlfile.status_code != 200:
                self._log_ugoira_meta_failure(pid, htmlfile, meta_trace, first_try_resp)
                return None
            htmlfile.raise_for_status()
            if self._stats_collector is not None:
                label = _cookie_usage_label(pid_cookie, self.cookie_pool, self._cookie_alias_map)
                self._stats_collector.report_request(label)
            try:
                gif_info=json.loads(htmlfile.content)['body']
                download_url=gif_info['originalSrc']
                delay_info=[item["delay"] for item in gif_info["frames"]]
                url=download_url
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"[pixiv_thread] PID {pid} JSON parse failed: {e}")
                print(f"[pixiv_thread] response preview: {htmlfile.text[:500]}")
                return None
            headers = self._build_artwork_headers(pid, pid_cookie, need_cookie, honour_pid_used=True)
            htmlfile = http.get(url,headers=headers,stream=True)
            my_time=self.download_time
            zip_bytes = None
            if htmlfile.status_code == 200: # 請求成功才開始下載資料
                    #print('Start download,[File size]:{size:.2f} MB'.format(size = content_size / chunk_size /1024)) # 偵錯用：可印出檔案大小
                    self.timelock.acquire()
                    
                    self.download_time= self.download_time+datetime.timedelta(seconds=1)
                    print(datetime.datetime.strftime(self.download_time,'%Y-%m-%d %H:%M:%S'))
                    self.timelock.release()
                    chunks = []
                    for data in htmlfile.iter_content(chunk_size=65536):
                        if data:
                            chunks.append(data)
                    zip_bytes = b"".join(chunks)
                    if self._stats_collector is not None and zip_bytes:
                        self._stats_collector.report_bytes(len(zip_bytes))
                        label = _cookie_usage_label(pid_cookie, self.cookie_pool, self._cookie_alias_map)
                        self._stats_collector.report_request(label)
            if not zip_bytes:
                return [url,my_time.strftime('%Y%m%d_%H%M%S')]
            frame_blobs = []
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zipo:
                for member in zipo.namelist():
                    if member.endswith('/'):
                        continue
                    frame_blobs.append(zipo.read(member))
            if not frame_blobs:
                return [url,my_time.strftime('%Y%m%d_%H%M%S')]
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
                )
            except Exception:
                name = 'illust_' + pid + my_time.strftime('_%Y%m%d_%H%M%S.gif')
            target_dir = self._resolve_download_target_dir(tag, pid, media_kind='GIF')
            saved_gif_path = os.path.join(target_dir, name)
            self._save_ugoira_gif(frame_blobs, saved_gif_path, delay_info)
            if self._stats_collector is not None:
                self._stats_collector.report_file(True)
            self._convert_file_to_jxl(saved_gif_path)
            return 0
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError):
            # Network/proxy failures must propagate so the scheduler-aware caller
            # can disable the cookie/proxy for this run.
            raise
        except Exception as err:
            print(err,self.cookies)
        return [url,my_time.strftime('%Y%m%d_%H%M%S')]

    def jpg_download(self,url, session=None):
        self.timelock.acquire()
        timetag=self.download_time.strftime('%Y%m%d_%H%M%S')
        self.download_time += datetime.timedelta(seconds=1)
        self.timelock.release()
        last_err = None
        for i in range (0,5): # 最多重試 5 次，失敗就回傳錯誤
            try:
                pid, pid_cookie, need_cookie = self._resolve_pid_and_cookie(url, source="step4")
                normalized = self._load_artwork_metadata(pid, pid_cookie)
                if not normalized:
                    raise ValueError("Pixiv_info 回傳格式異常")
                tag, like, pagecount, img_url = normalized

                if(like==404 and tag ==404):
                    return
                p=str(url).rsplit('_',1)[1].rsplit('.',1)[0]    # 作品頁碼
                picture_format = url.rsplit('.',1)[1]
                headers = self._build_artwork_headers(pid, pid_cookie, need_cookie)
                # jpg uses a different User-Agent than gif
                headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.82 Safari/537.36'
                try:
                    self._pid_cookie_used[str(pid)] = bool(need_cookie is True and pid_cookie)
                except Exception:
                    pass
                http = session if session is not None else requests
                htmlfile = http.get(url,headers=headers,stream=True,timeout=5)
                htmlfile.raise_for_status()
                if self._stats_collector is not None:
                    label = _cookie_usage_label(pid_cookie, self.cookie_pool, self._cookie_alias_map)
                    self._stats_collector.report_request(label)
                size = 0
                chunk_size = 1024
                name=''
                if htmlfile.status_code == 200: #?斗??臬???????
                    try:
                        hashtag = self._build_hashtag_text(tag, max_len=230)
                        name = self._build_download_filename(
                            pid,
                            page_suffix=p,
                            ext=picture_format,
                            hashtag=hashtag,
                            timetag=timetag,
                            notag=self.notag,
                            notime=self.notime,
                        )
                    except Exception:
                        name = 'illust_' + pid + p + timetag + '.' + picture_format
                    tag=str(tag)
                    target_dir = self._resolve_download_target_dir(tag, pid)
                    filepath = os.path.join(target_dir, name)
                    with open(filepath,'wb') as file: # 寫入圖片檔案
                        for data in htmlfile.iter_content(chunk_size = chunk_size):
                            file.write(data)
                            size +=len(data)
                    if self._stats_collector is not None:
                        self._stats_collector.report_bytes(size)
                        self._stats_collector.report_file(True)
                    self._convert_file_to_jxl(filepath)
                return 0
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

    def stop(self):
        if self.single_mode_flag:
            self._stop_after_group = True
            self._stopped_by_request = True
            # 若目前在暫停中，先解除暫停，才能在當前 PID 組完成後停下
            if not self._pause_event.is_set():
                self._pause_event.set()
                try:
                    self._q.put(WorkerEvent("output", "<p><font color='orange'>收到中止要求：已解除暫停，將在當前 PID 組完成後停止</font></p>"))
                except Exception:
                    pass
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



