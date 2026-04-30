import time
import json
import os
import datetime
import re
import glob
import random as pyrandom
import threading
from queue import Queue
from pixiv_api import *
from app.core.worker_event import WorkerEvent
import tag_edit
import pixiv_api
from app.core.pixiv_thread_utils import (
    append_diagnostic_event,
    apply_cookie_pool_speedup,
    atomic_write_json,
    atomic_write_text,
    canonicalize_pximg_url_for_storage,
    cookie_speed_divisor,
    cookie_usage_label,
    init_cookie_fields,
    normalize_pid,
    normalize_pid_set,
    output_err,
    read_pid_lines,
    safe_read_json,
)
from app.core.pixiv_thread_base import (
    PauseableThread,
    _normalize_special_like_rules,
    _resolve_like_threshold,
    _cookie_usage_label,
    _format_cookie_usage_summary,
)

class get_img_url_thread(PauseableThread):
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
        pid_wait_min=10,
        pid_wait_max=60,
        pid_wait_nocookie_min=1,
        pid_wait_nocookie_max=6,
        special_like_rules=None,
    ):
        super().__init__(q)
        self.Author_list=Author_list
        self.Agent=Agent
        self.cookie_entries, self.cookie_pool, self._cookie_alias_map, self.cookies = init_cookie_fields(cookies)
        self._pid_cookie_selection = {}
        self._pid_cookie_alias_selection = {}
        self.exist_pid = normalize_pid_set(exist_pid)
        self.ban_tag=ban_tag
        self.must_tag=must_tag
        self.like_num=like_num
        self.special_like_rules = _normalize_special_like_rules(special_like_rules)
        self.no_to_check=no_to_check
        self._ban_tag_norm = self._normalize_filter_tags(self.ban_tag)
        self._must_tag_norm = self._normalize_filter_tags(self.must_tag)
        if isinstance(base_path, str) and base_path.strip():
            self.path = base_path
        self.tag_queue=Queue()
        self.like_queue=Queue()
        self._step3_filter_skip_counts = {"ban_tag": 0, "must_tag": 0, "like": 0}
        self._step3_filter_skip_notice_emitted = False
        self._step3_filter_skip_every = 200
        self._step3_query_counts = {"network": 0, "cache": 0, "skip": 0}
        self._step3_cookie_req_counts = {"need": 0, "free": 0, "unknown": 0}
        self._step3_wait_applied_count = 0
        self._step3_query_notice_every = 200
        self.single_mode_flag = bool(single_thread_mode)
        try:
            self.pid_wait_min = int(pid_wait_min)
            self.pid_wait_max = int(pid_wait_max)
        except Exception:
            self.pid_wait_min, self.pid_wait_max = 10, 60
        try:
            self.pid_wait_nocookie_min = int(pid_wait_nocookie_min)
            self.pid_wait_nocookie_max = int(pid_wait_nocookie_max)
        except Exception:
            self.pid_wait_nocookie_min, self.pid_wait_nocookie_max = 1, 6
        if self.pid_wait_min < 1:
            self.pid_wait_min = 1
        if self.pid_wait_max < self.pid_wait_min:
            self.pid_wait_max = self.pid_wait_min
        if self.pid_wait_nocookie_min < 0:
            self.pid_wait_nocookie_min = 0
        if self.pid_wait_nocookie_max < self.pid_wait_nocookie_min:
            self.pid_wait_nocookie_max = self.pid_wait_nocookie_min
        self.url_meta = {}
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
        meta = safe_read_json(self.url_meta_path, {})
        self.url_meta = meta if isinstance(meta, dict) else {}
        self._migrate_url_meta_schema()
        self._revoked_pid_set = set(read_pid_lines(self.revoked_pid_path))
        try:
            req_path = os.path.join(self.path, 'pixiv_cookie_requirement.json')
            req_data = safe_read_json(req_path, {})
            if isinstance(req_data, dict):
                for _pid, _entry in req_data.items():
                    if isinstance(_entry, dict):
                        self._cookie_requirement_map[str(_pid)] = _entry.get('requires_cookie')
        except Exception:
            self._cookie_requirement_map = {}
        #print(self.no_to_check)
        self._diag(
            "step3_init",
            exist_pid_count=len(self.exist_pid),
            url_meta_count=len(self.url_meta),
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
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                return sum(1 for line in f if str(line).strip())
        except Exception:
            return 0

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

    def _load_saved_cookie_requirement_map(self):
        merged = {}
        primary_candidates = []
        try:
            p = os.path.join(self.path, "pixiv_cookie_requirement.json")
            if p not in primary_candidates:
                primary_candidates.append(p)
        except Exception:
            pass
        try:
            appdata_root = os.path.join(os.getenv('APPDATA') or "", "pixiv_download")
            p = os.path.join(appdata_root, "pixiv_cookie_requirement.json")
            if p not in primary_candidates:
                primary_candidates.append(p)
        except Exception:
            pass

        history_candidates = []
        for base_file in list(primary_candidates):
            try:
                hist_dir = os.path.join(os.path.dirname(base_file), "history")
                pattern = os.path.join(hist_dir, os.path.basename(base_file) + ".*")
                found = sorted(glob.glob(pattern), key=lambda x: os.path.getmtime(x), reverse=True)
                for fp in found:
                    if fp not in history_candidates:
                        history_candidates.append(fp)
            except Exception:
                pass

        for file_path in list(primary_candidates) + list(history_candidates):
            try:
                if not os.path.isfile(file_path):
                    continue
                with open(file_path, encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                for raw_pid, entry in data.items():
                    pid_key = normalize_pid(raw_pid)
                    if not pid_key:
                        continue
                    req = None
                    if isinstance(entry, dict):
                        req = entry.get("requires_cookie", None)
                    if req not in (True, False, None):
                        continue
                    # Prefer primary file values over history fallback values.
                    if pid_key not in merged:
                        merged[pid_key] = req
                    elif merged.get(pid_key) is None and req in (True, False):
                        merged[pid_key] = req
            except Exception:
                pass
        return merged

    def _migrate_url_meta_schema(self):
        changed = False
        try:
            if not isinstance(self.url_meta, dict):
                return False
            saved_req_map = self._load_saved_cookie_requirement_map()
            try:
                if not isinstance(self._cookie_requirement_map, dict):
                    self._cookie_requirement_map = {}
                for _pid, _req in saved_req_map.items():
                    if _pid not in self._cookie_requirement_map:
                        self._cookie_requirement_map[_pid] = _req
            except Exception:
                pass

            sentinel = object()
            for pid_key, meta in list(self.url_meta.items()):
                if not isinstance(meta, dict):
                    continue

                pid_norm = normalize_pid(pid_key) or str(pid_key)
                pinfo = meta.get("pixiv_info")
                req = meta.get("requires_cookie", None)

                if req is None and isinstance(pinfo, dict):
                    req = pinfo.get("requires_cookie", None)

                if req is None and isinstance(saved_req_map, dict):
                    req = saved_req_map.get(pid_norm, None)

                if meta.get("requires_cookie", sentinel) != req:
                    meta["requires_cookie"] = req
                    changed = True

                if not isinstance(pinfo, dict):
                    pinfo = {
                        "tag": meta.get("tag", []) if isinstance(meta.get("tag"), list) else [],
                        "like": meta.get("like", 0),
                        "pagecount": meta.get("pagecount", 0),
                        "img_url": meta.get("img_url", None),
                        "requires_cookie": req,
                        "queried_at": "",
                        "source": "migrated",
                    }
                    meta["pixiv_info"] = pinfo
                    self.url_meta[pid_key] = meta
                    changed = True
                    continue

                if isinstance(pinfo, dict):
                    if pinfo.get("requires_cookie", sentinel) != req:
                        pinfo["requires_cookie"] = req
                        meta["pixiv_info"] = pinfo
                        self.url_meta[pid_key] = meta
                        changed = True
            if changed:
                atomic_write_json(self.url_meta_path, self.url_meta, backup=True)
        except Exception:
            return False
        return changed

    def _set_requires_cookie_meta(self, pid, need_cookie):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            if not isinstance(self.url_meta, dict):
                return
            meta = self.url_meta.get(pid_key, {})
            if not isinstance(meta, dict):
                meta = {}
            meta["requires_cookie"] = need_cookie
            pixiv_info = meta.get("pixiv_info")
            if isinstance(pixiv_info, dict):
                pixiv_info["requires_cookie"] = need_cookie
                meta["pixiv_info"] = pixiv_info
            self.url_meta[pid_key] = meta
        except Exception:
            pass

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
            if not s:
                continue
            out.append(s.lower())
        return list(dict.fromkeys(out))

    def _normalize_artwork_tags(self, tag):
        if isinstance(tag, list):
            source = tag
        elif tag in (None, 404):
            source = []
        else:
            source = [tag]
        result = []
        for t in source:
            s = str(t).strip()
            if s:
                result.append(s.lower())
        return result

    def _tag_hit(self, target_tag, artwork_tags):
        key = str(target_tag).strip().lower()
        if not key:
            return False
        for item in artwork_tags:
            if key in item:
                return True
        return False

    def _record_step3_filter_skip(self, reason, pid_key=None):
        key = str(reason or "other")
        try:
            if key not in self._step3_filter_skip_counts:
                self._step3_filter_skip_counts[key] = 0
            self._step3_filter_skip_counts[key] += 1
        except Exception:
            pass
        try:
            self._diag("step3_filter_skip", reason=key, pid=str(pid_key or ""))
        except Exception:
            pass
        total = 0
        try:
            total = int(sum(int(v or 0) for v in self._step3_filter_skip_counts.values()))
        except Exception:
            total = 0
        try:
            if not self._step3_filter_skip_notice_emitted:
                self._step3_filter_skip_notice_emitted = True
                self._q.put(WorkerEvent("output",
                    "<p><font color='gray'>[Step3過濾] 已啟用精簡輸出；詳細 PID 可查看 tag_ban_pid.txt 與 pid_num_pid.txt</font></p>"
                ))
            if total > 0 and total % int(self._step3_filter_skip_every) == 0:
                self._q.put(WorkerEvent("output",
                    "<p><font color='gray'>[Step3過濾摘要] 已略過 {} 筆（標籤={}、必含標籤={}、低愛心={}）</font></p>".format(
                        total,
                        int(self._step3_filter_skip_counts.get("ban_tag", 0)),
                        int(self._step3_filter_skip_counts.get("must_tag", 0)),
                        int(self._step3_filter_skip_counts.get("like", 0)),
                    )
                ))
        except Exception:
            pass

    def _emit_step3_filter_skip_final_summary(self):
        try:
            total = int(sum(int(v or 0) for v in self._step3_filter_skip_counts.values()))
        except Exception:
            total = 0
        if total <= 0:
            return
        try:
            self._q.put(WorkerEvent("output",
                "<p><font color='gray'>[Step3過濾完成] 共略過 {} 筆（標籤={}、必含標籤={}、低愛心={}）</font></p>".format(
                    total,
                    int(self._step3_filter_skip_counts.get("ban_tag", 0)),
                    int(self._step3_filter_skip_counts.get("must_tag", 0)),
                    int(self._step3_filter_skip_counts.get("like", 0)),
                )
            ))
        except Exception:
            pass

    def _record_step3_query_result(self, query_source, need_cookie=None, wait_applied=False):
        source = str(query_source or "skip").strip().lower()
        if source not in self._step3_query_counts:
            source = "skip"

        try:
            self._step3_query_counts[source] += 1
        except Exception:
            pass

        try:
            if need_cookie is True:
                self._step3_cookie_req_counts["need"] += 1
            elif need_cookie is False:
                self._step3_cookie_req_counts["free"] += 1
            else:
                self._step3_cookie_req_counts["unknown"] += 1
        except Exception:
            pass

        try:
            if bool(wait_applied):
                self._step3_wait_applied_count += 1
        except Exception:
            pass

        total = 0
        try:
            total = int(sum(int(v or 0) for v in self._step3_query_counts.values()))
        except Exception:
            total = 0
        try:
            if total > 0 and total % int(self._step3_query_notice_every) == 0:
                self._emit_step3_query_final_summary(final=False)
        except Exception:
            pass

    def _emit_step3_query_final_summary(self, final=True):
        try:
            total = int(sum(int(v or 0) for v in self._step3_query_counts.values()))
        except Exception:
            total = 0
        if total <= 0:
            return

        color = "gray" if final else "black"
        label = "Step3查詢完成" if final else "Step3查詢摘要"
        try:
            self._q.put(WorkerEvent("output",
                "<p><font color='{}'>[{}] 已處理 {} 筆（網路查詢={}、快取={}、未查詢={}、等待執行={}；requires_cookie: 需要={}、不需要={}、未知={}）</font></p>".format(
                    color,
                    label,
                    total,
                    int(self._step3_query_counts.get("network", 0)),
                    int(self._step3_query_counts.get("cache", 0)),
                    int(self._step3_query_counts.get("skip", 0)),
                    int(self._step3_wait_applied_count or 0),
                    int(self._step3_cookie_req_counts.get("need", 0)),
                    int(self._step3_cookie_req_counts.get("free", 0)),
                    int(self._step3_cookie_req_counts.get("unknown", 0)),
                )
            ))
        except Exception:
            pass

    def _passes_artwork_filters(self, pid_key, tag, like):
        artwork_tags = self._normalize_artwork_tags(tag)

        for blocked in self._ban_tag_norm:
            if self._tag_hit(blocked, artwork_tags):
                try:
                    self.tag_queue.put(str(pid_key))
                except Exception:
                    pass
                self._record_step3_filter_skip("ban_tag", pid_key=pid_key)
                return False, "ban_tag"

        if self._must_tag_norm:
            ok = False
            for required in self._must_tag_norm:
                if self._tag_hit(required, artwork_tags):
                    ok = True
                    break
            if not ok:
                try:
                    self.tag_queue.put(str(pid_key))
                except Exception:
                    pass
                self._record_step3_filter_skip("must_tag", pid_key=pid_key)
                return False, "must_tag"

        like_limit, _matched_rules = _resolve_like_threshold(
            self.like_num,
            artwork_tags,
            self.special_like_rules,
            self._tag_hit,
            self._to_int,
        )
        like_value = self._to_int(like, None)
        if like_limit > 0 and like_value is not None and like_value < like_limit:
            try:
                self.like_queue.put(str(pid_key))
            except Exception:
                pass
            self._record_step3_filter_skip("like", pid_key=pid_key)
            return False, "like"

        return True, "pass"

    def _mark_revoked_pid(self, pid, reason="404"):
        pid_key = normalize_pid(pid)
        if not pid_key:
            return
        if pid_key in self._revoked_pid_set:
            return
        self._revoked_pid_set.add(pid_key)
        self._revoked_pid_new.add(pid_key)
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='orange'>PID {pid_key} 已標記為失效（{reason}），後續會自動略過</font></p>"
            ))
        except Exception:
            pass

    def _flush_revoked_pid_file(self):
        try:
            if not self._revoked_pid_set:
                return
            all_pids = sorted(self._revoked_pid_set)
            try:
                atomic_write_text(self.revoked_pid_path, all_pids, backup=True)
            except Exception:
                with open(self.revoked_pid_path, "w", encoding="utf-8") as f:
                    f.writelines([str(x) + "\n" for x in all_pids])
        except Exception:
            pass

    def _sleep_ultra_slow(self, pid, need_cookie=None):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            if bool(self._pid_cache_hit.get(pid_key, False)):
                return
        except Exception:
            pass
        try:
            self._countdown_pid = pid_key
        except Exception:
            pass
        if need_cookie is False:
            delay = pyrandom.randint(self.pid_wait_nocookie_min, self.pid_wait_nocookie_max)
        else:
            delay = pyrandom.randint(self.pid_wait_min, self.pid_wait_max)
        delay = apply_cookie_pool_speedup(delay, self.cookie_pool)
        for _ in range(delay):
            if self._stop_event.is_set():
                break
            self._pause_event.wait()
            try:
                self._q.put(WorkerEvent("countdown", delay - _))
            except Exception:
                pass
            time.sleep(1)
        try:
            self._q.put(WorkerEvent("countdown", 0))
        except Exception:
            pass

    def _extract_pid_from_url(self, url):
        try:
            filename = str(url).rsplit('/', 1)[1]
            return str(filename.split('_', 1)[0])
        except Exception:
            return None

    def _cookie_label_for_pid(self, pid, need_cookie=None):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            alias = str(self._pid_cookie_alias_selection.get(pid_key, "") or "").strip()
            if alias:
                return alias
        except Exception:
            pass
        try:
            selected = str(self._pid_cookie_selection.get(pid_key, "") or "").strip()
            if selected:
                resolved = cookie_usage_label(selected, self.cookie_pool, self._cookie_alias_map)
                if resolved:
                    return resolved
        except Exception:
            pass
        try:
            if self.cookie_pool:
                resolved = cookie_usage_label(self.cookie_pool[0], self.cookie_pool, self._cookie_alias_map)
                if resolved:
                    return resolved
        except Exception:
            pass
        if need_cookie is False and not str(getattr(self, "cookies", "") or "").strip():
            return "免Cookie"
        if str(getattr(self, "cookies", "") or "").strip():
            return "單一Cookie"
        return "未提供Cookie"

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
            self._pid_cookie_alias_selection[pid_key] = cookie_usage_label(selected, self.cookie_pool, self._cookie_alias_map)
        except Exception:
            pass
        return selected

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

    def _flush_url_meta_snapshot(self):
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

        try:
            meta = self.url_meta.get(pid_key, {}) if isinstance(self.url_meta, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            self._set_requires_cookie_meta(pid_key, True)
            meta = self.url_meta.get(pid_key, {}) if isinstance(self.url_meta, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["cookie_used"] = True
            meta["cookie_used_source"] = str(source)
            meta["cookie_used_updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.url_meta[pid_key] = meta
        except Exception:
            pass

        try:
            atomic_write_json(self.url_meta_path, self.url_meta, backup=True)
        except Exception:
            self._flush_url_meta_snapshot()

        signal_obj = self.__dict__.get("_output", None)
        if signal_obj is None:
            try:
                signal_obj = getattr(self, "_output", None)
            except Exception:
                signal_obj = None
        try:
            if signal_obj is not None and hasattr(signal_obj, "emit"):
                signal_obj.emit(
                    f"<p><font color='blue'>[GIF][Cookie] PID {pid_key} 使用 cookies（來源：{source}），已更新 all_url_meta 暫存</font></p>"
                )
        except Exception:
            pass

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

    def _write_all_url_snapshot(self, fetched_urls):
        """Write merged all_url snapshot to disk."""
        try:
            file_path = self.path
            file_name = "all_url.txt"
            old_urls = []
            try:
                with open(file_path + "/" + file_name, encoding='utf-8') as f:
                    old_urls = [line.rstrip() for line in f if line.rstrip()]
            except Exception:
                old_urls = []

            def _keep_not_downloaded(url):
                pid = self._extract_pid_from_url(url)
                return (pid is None) or (pid not in self.exist_pid)

            old_urls = [canonicalize_pximg_url_for_storage(u) for u in old_urls if _keep_not_downloaded(u)]
            new_urls = [
                canonicalize_pximg_url_for_storage(u)
                for u in fetched_urls
                if isinstance(u, str) and ('https' in u) and _keep_not_downloaded(u)
            ]

            merged = []
            seen = set()
            for u in old_urls + new_urls:
                if u in seen:
                    continue
                seen.add(u)
                merged.append(u)

            try:
                os.makedirs(file_path, exist_ok=True)
            except Exception:
                pass
            # 同步刷新 all_url.txt 與 all_url_meta.json
            write_ok = self._write_all_url_file(merged, reason="step3_snapshot")
            self._diag(
                "step3_snapshot_merged",
                old_count=len(old_urls),
                new_count=len(new_urls),
                merged_count=len(merged),
                write_ok=bool(write_ok),
            )
            return old_urls, new_urls, merged
        except Exception as err:
            self._diag("step3_snapshot_failed", error=str(err))
            return [], [], []

    def _on_pause_hook(self):
        self._flush_url_meta_snapshot()

    def _on_stop_hook(self):
        self._flush_url_meta_snapshot()

    def check_exist(self):
        file_candidates = []
        primary_path = os.path.join(self.path, "pictures_id.txt")
        file_candidates.append(primary_path)
        try:
            appdata_path = os.path.join(os.getenv('APPDATA') + r'/pixiv_download/', 'pictures_id.txt')
            if appdata_path not in file_candidates:
                file_candidates.append(appdata_path)
        except Exception:
            pass

        block_set = set()
        try:
            if isinstance(self.no_to_check, list):
                block_set = normalize_pid_set(self.no_to_check)
        except Exception:
            block_set = set()

        step2_skip_set = set()
        step2_skip_file = os.path.join(self.path, "step2_skip_pid.txt")
        try:
            if os.path.isfile(step2_skip_file):
                with open(step2_skip_file, encoding="utf-8", errors="ignore") as f:
                    step2_skip_set = normalize_pid_set([line.rstrip() for line in f if str(line).strip()])
        except Exception:
            step2_skip_set = set()

        last_err = None
        for pic_path in file_candidates:
            if not os.path.isfile(pic_path):
                continue
            try:
                pictures_id = []
                excluded_by_skip_file = 0
                excluded_by_step2_skip = 0
                raw_count = 0
                try:
                    with open(pic_path, encoding='utf-8') as file:
                        for line in file:
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
                except UnicodeDecodeError:
                    with open(pic_path, encoding='utf-8', errors='ignore') as file:
                        for line in file:
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
                try:
                    self._q.put(WorkerEvent("output", f"<p><font color='gray'>pictures_id 來源: {pic_path}</font></p>"))
                    self._q.put(WorkerEvent("output",
                        f"<p><font color='gray'>[TaskFilter][Step3-Pre] pictures_id原始={raw_count}, skip_file排除={excluded_by_skip_file}, 待去重={len(pictures_id)}</font></p>"
                    ))
                    self._q.put(WorkerEvent("output",
                        f"<p><font color='gray'>[TaskFilter][Step3-Pre] 這次從 step2_skip_pid.txt 排除 {excluded_by_step2_skip} 筆（步驟2提前跳過，所以之前沒有存下來）</font></p>"
                    ))
                except Exception:
                    pass
                self._diag(
                    "step3_skip_file_prefilter",
                    source_path=str(pic_path),
                    raw_count=int(raw_count),
                    skipped_no_to_check=int(excluded_by_skip_file),
                    skipped_step2_skip_file=int(excluded_by_step2_skip),
                    post_skip_file_count=int(len(pictures_id)),
                )
                return pictures_id
            except Exception as err:
                last_err = err

        try:
            detail = "" if last_err is None else f" ({last_err})"
            self._q.put(WorkerEvent("output", "<p><font color='red'>找不到 pictures_id.txt: {}</font></p>".format(' | '.join(file_candidates))))
            self._q.put(WorkerEvent("output", f"<p><font color='red'>讀取失敗: {detail}</font></p>"))
        except Exception:
            pass
        self._q.put(WorkerEvent("finished", 'Task finished'))
        self._q.put(WorkerEvent("next", -1))
        return 0

    def _resolve_pictures_id_file_path(self):
        candidates = [os.path.join(self.path, "pictures_id.txt")]
        try:
            appdata_path = os.path.join(os.getenv('APPDATA') + r'/pixiv_download/', 'pictures_id.txt')
            if appdata_path not in candidates:
                candidates.append(appdata_path)
        except Exception:
            pass
        for p in candidates:
            try:
                if os.path.isfile(p):
                    return p
            except Exception:
                pass
        return candidates[0]

    def _persist_pending_pid_file(self):
        """Flush the pending-PID set to disk.

        Phase 36: this used to be called per-PID with backup=True, costing
        O(N^2) total disk I/O (sort + atomic_write_text + shutil.copy2 of the
        whole file each time). It now runs only on the every-100-PID batch
        flush in ``_run_processing_loop`` and on finalize, with backup=False
        because pictures_id.txt is a runtime pending-list, not user data
        worth keeping in history/.
        """
        try:
            lines = sorted(
                [str(x) for x in (self._pending_pid_remaining or set()) if str(x).strip()],
                key=lambda s: int(s) if str(s).isdigit() else str(s),
            )
            atomic_write_text(self._pending_pid_file_path, lines, backup=False)
        except Exception:
            pass

    def _init_pending_pid_tracker(self, fallback_pid_list, reset_with_fallback=False):
        self._pending_pid_file_path = self._resolve_pictures_id_file_path()
        loaded = set()
        if not bool(reset_with_fallback):
            try:
                with open(self._pending_pid_file_path, encoding='utf-8', errors='ignore') as f:
                    loaded = normalize_pid_set([line.rstrip() for line in f if str(line).strip()])
            except Exception:
                loaded = set()
        if not loaded:
            loaded = normalize_pid_set(fallback_pid_list)
        self._pending_pid_remaining = set(loaded)
        self._persist_pending_pid_file()

    def _mark_pid_processed(self, pid):
        """Phase 36: only update the in-memory set; the disk flush happens
        on the every-100-PID batch boundary in ``_run_processing_loop`` and
        on finalize. Per-PID disk writes here were O(N^2)."""
        pid_key = normalize_pid(pid)
        if not pid_key:
            return
        try:
            with self._pending_pid_lock:
                self._pending_pid_remaining.discard(pid_key)
        except Exception:
            pass

    def _is_pid_cached_meta(self, pid):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            meta = self.url_meta.get(pid_key, {}) if isinstance(self.url_meta, dict) else {}
            if not isinstance(meta, dict) or not meta:
                return False, {}
            img_url = str(meta.get("img_url", "") or "").strip()
            pagecount = self._to_int(meta.get("pagecount", 0), 0) or 0
            if not img_url or img_url == "None":
                return False, meta
            if pagecount <= 0:
                return False, meta
            return True, meta
        except Exception:
            return False, {}

    def _build_cached_urls_from_meta(self, pid, meta):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            img_url = str((meta or {}).get("img_url", "") or "").strip()
            if not img_url or "." not in img_url:
                return []
            page_total = self._to_int((meta or {}).get("pagecount", 1), 1) or 1
            if page_total < 1:
                page_total = 1
            left, right = img_url.rsplit(".", 1)
            # Support "..._p.jpg", "..._p0.jpg", "..._ugoira0.zip" formats.
            left_norm = re.sub(r"_(p|ugoira)\d+$", r"_\1", left, flags=re.IGNORECASE)
            if re.search(r"_(p|ugoira)$", left_norm, flags=re.IGNORECASE):
                return [left_norm + str(idx) + "." + right for idx in range(page_total)]
            # Fallback to legacy behavior.
            return [left + str(idx) + "." + right for idx in range(page_total)]
        except Exception:
            try:
                self._diag("step3_cached_url_build_failed", pid=str(pid_key))
            except Exception:
                pass
            return []

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
            has_cache, meta = self._is_pid_cached_meta(pid_key)
            if not has_cache:
                self._pid_cache_hit[pid_key] = False
                network_pids.append(pid_key)
                continue

            self._pid_cache_hit[pid_key] = True
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

            tag = meta.get("tag", []) if isinstance(meta, dict) else []
            like = meta.get("like", 0) if isinstance(meta, dict) else 0
            passed, reason = self._passes_artwork_filters(pid_key, tag, like)
            try:
                if isinstance(self.url_meta.get(pid_key), dict):
                    self.url_meta[pid_key]["filter_pass"] = bool(passed)
                    self.url_meta[pid_key]["filter_reason"] = str(reason)
            except Exception:
                pass
            if not passed:
                stats["cached_filtered"] += 1
                continue

            one_pid_urls = self._build_cached_urls_from_meta(pid_key, meta)
            if not one_pid_urls:
                self._pid_cache_hit[pid_key] = False
                network_pids.append(pid_key)
                stats["cached_fallback_network"] += 1
                continue

            cached_urls.extend(one_pid_urls)
            stats["cached_hit_pid"] += 1
            stats["cached_generated_url"] += len(one_pid_urls)
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
        try:
            self._q.put(WorkerEvent("output", f"<p><font color='gray'>[TaskFilter][Step3] input={raw_pid_count}, skipped_no_to_check={skipped_no_to_check}, skipped_exist={skipped_exist}, skipped_revoked={skipped_revoked}, duplicate={duplicate_count}, invalid={invalid_count}, cached_hit_pid={0}, cached_generated_url={0}, cached_filtered={0}, cached_fallback_network={0}, pending_network={self.pid_max}</font></p>"))
        except Exception:
            pass
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
            f"<p><font color='green'>URL階段等待策略：僅網路查詢PID等待；快取PID不等待（需Cookie {self.pid_wait_min}~{self.pid_wait_max} 秒；免Cookie {self.pid_wait_nocookie_min}~{self.pid_wait_nocookie_max} 秒）</font></p>"
        ))
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>URL階段多Cookie加速：{len(self.cookie_pool or [])} 組 cookies，等待加速係數 x{cookie_speed_divisor(self.cookie_pool):.2f}</font></p>"
            ))
        except Exception:
            pass
        task_queue = Queue()
        for pid in pictures_id:
            task_queue.put(pid)
        self._q.put(WorkerEvent("output", "<p><font color='green'>URL階段採用消費者隊列模式，PID 會在查詢後自動從 pictures_id.txt 移除</font></p>"))
        return task_queue

    def _run_processing_loop(self, task_queue):
        results = []
        processed_count = 0
        progress_every = 100
        flush_every = 100

        while (not task_queue.empty()) and not self._stop_event.is_set():
            try:
                pid = task_queue.get_nowait()
            except Exception:
                break

            if processed_count % progress_every == 0:
                try:
                    self._q.put(WorkerEvent("output", f"<p><font color='black'>URL階段進度：{processed_count + 1}/{self.pid_max} (PID {pid})</font></p>"))
                except Exception:
                    pass

            one = self.get_download_url(self.path, self.Agent, 1, pid)
            if isinstance(one, list):
                results.append(one)
            elif isinstance(one, str):
                if one.startswith('http'):
                    results.append([one])
                else:
                    results.append([])

            if not self._stop_event.is_set():
                self._mark_pid_processed(pid)

            processed_count += 1
            task_queue.task_done()

            if processed_count % flush_every == 0 or processed_count == self.pid_max:
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
                    self._q.put(WorkerEvent("output", f"<p><font color='gray'>[分批寫入] 已處理 {processed_count}/{self.pid_max}，all_url 目前 {len(merged)} 筆（本批新增 {len(new_urls)}），pictures_id 剩餘 {remain}</font></p>"))
                except Exception:
                    pass

        return results

    def _finalize_on_stop(self, results):
        try:
            flat_results = [x for item in results if isinstance(item, list) for x in item]
            old_urls, new_urls, merged = self._write_all_url_snapshot(flat_results)
            self._flush_url_meta_snapshot()
            self._persist_pending_pid_file()  # Phase 36: ensure pending list is saved on stop
            self._flush_revoked_pid_file()
            self._emit_step3_filter_skip_final_summary()
            self._emit_step3_query_final_summary(final=True)
            self._emit_cookie_usage_summary("step3", "Step3 Cookie統計")
            self._diag(
                "step3_stopped",
                collected_url_count=len(flat_results),
                merged_count=len(merged),
                added_count=len(new_urls),
            )
            self._q.put(WorkerEvent("output", f"<p><font color='orange'>已停止，已暫存 all_url {len(merged)} 筆（新增 {len(new_urls)}）</font></p>"))
        except Exception:
            pass
        self._q.put(WorkerEvent("finished", 'Task finished'))
        self._q.put(WorkerEvent("next", -1))

    def _finalize_on_complete(self, results):
        results = [i for item in results if isinstance(item, list) for i in item]
        error_pid = [i for i in results if 'https' not in i]
        results = [i for i in results if 'https' in i]
        old_urls, new_urls, merged = self._write_all_url_snapshot(results)
        self._persist_pending_pid_file()  # Phase 36: flush remaining pending PIDs
        self._diag(
            "step3_completed",
            old_count=len(old_urls),
            new_count=len(new_urls),
            merged_count=len(merged),
            error_pid_count=len(error_pid),
        )
        try:
            self._q.put(WorkerEvent("output", f"<p><font color='green'>all_url 寫入完成：舊URL {len(old_urls)} 筆、新URL {len(new_urls)} 筆、合併後 {len(merged)} 筆</font></p>"))
        except Exception:
            pass
        if len(new_urls) == 0:
            self._q.put(WorkerEvent("output", "<p><font color='gray'>沒有新的 URL，已保留原 all_url.txt</font></p>"))
        try:
            try:
                atomic_write_json(self.url_meta_path, self.url_meta)
            except Exception:
                with open(self.url_meta_path, 'w', encoding='utf-8') as f:
                    json.dump(self.url_meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._q.put(WorkerEvent("output", f"<p><font color='red'>寫入 all_url_meta.json 失敗: {e}</font></p>"))
        file_path = self.path
        tag_err = [self.tag_queue.get() for _ in range(self.tag_queue.qsize())]
        try:
            from safe_io import atomic_append_text
            atomic_append_text(os.path.join(file_path, "tag_ban_pid.txt"), tag_err)
        except Exception:
            try:
                with open(file_path + "/tag_ban_pid.txt", "a+") as f:
                    f.writelines([str(text) + "\n" for text in tag_err])
            except Exception:
                pass
        like_err = [self.like_queue.get() for _ in range(self.like_queue.qsize())]
        try:
            from safe_io import atomic_append_text
            atomic_append_text(os.path.join(file_path, "pid_num_pid.txt"), like_err)
        except Exception:
            try:
                with open(file_path + "/pid_num_pid.txt", "a+") as f:
                    f.writelines([str(text) + "\n" for text in like_err])
            except Exception:
                pass
        try:
            from safe_io import atomic_write_text
            atomic_write_text(os.path.join(self.path, "net_err.txt"), [str(text) for text in error_pid])
        except Exception:
            try:
                with open(self.path + "/net_err.txt", "w+") as f:
                    for text in error_pid:
                        f.write(str(text) + '\n')
            except Exception:
                pass
        self._flush_revoked_pid_file()
        self._emit_step3_filter_skip_final_summary()
        self._emit_step3_query_final_summary(final=True)
        self._emit_cookie_usage_summary("step3", "Step3 Cookie統計")
        try:
            if self._revoked_pid_new:
                self._q.put(WorkerEvent("output",
                    f"<p><font color='orange'>本次新增失效 PID {len(self._revoked_pid_new)} 筆，已寫入 revoked_pid.txt</font></p>"
                ))
        except Exception:
            pass
        self._q.put(WorkerEvent("finished", '抓取所有PID完成'))
        self._q.put(WorkerEvent("next", 4))
        self._q.put(WorkerEvent("output", f"<p><font color='red'>Total URL count: {len(merged)}</font></p>"))

    def run(self):
        try:
            self._q.put(WorkerEvent("output", "URL階段開始"))
            self._reset_run_counters()
            pictures_id = self._load_and_filter_pid_list()
            if pictures_id is None:
                return
            task_queue = self._build_and_emit_task_queue(pictures_id)
            results = self._run_processing_loop(task_queue)
            if self._stop_event.is_set():
                self._finalize_on_stop(results)
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
        """Unpack a cached url_meta entry into the (tag, like, pagecount, img_url, need_cookie)
        tuple expected by `get_download_url`. Pure dict reads; no side effects."""
        tag = cached.get('tag', [])
        like = cached.get('like', 0)
        pagecount = int(cached.get('pagecount', 1) or 1)
        img_url = cached.get('img_url')
        need_cookie = cached.get('requires_cookie', None)
        return tag, like, pagecount, img_url, need_cookie

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
                                     need_cookie, artwork_url, query_source):
        """Write the url_meta entry for a successfully resolved PID. Wrapped in try/except
        to preserve the original best-effort semantics (failures must not abort the workflow)."""
        try:
            self.url_meta[pid_key] = {
                "tag": tag if isinstance(tag, list) else [],
                "like": int(like) if str(like).isdigit() else like,
                "pagecount": int(pagecount) if str(pagecount).isdigit() else pagecount,
                "img_url": img_url,
                "requires_cookie": need_cookie,
                "artwork_url": artwork_url,
                "pixiv_info": {
                    "tag": tag if isinstance(tag, list) else [],
                    "like": int(like) if str(like).isdigit() else like,
                    "pagecount": int(pagecount) if str(pagecount).isdigit() else pagecount,
                    "img_url": img_url,
                    "requires_cookie": need_cookie,
                    "queried_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source": query_source,
                },
            }
        except Exception:
            pass

    def get_download_url(self,path,Agent,num,pid):    # 取得單一作品的下載 URL
        self._pause_event.wait()
        if self._stop_event.is_set():
            return []
        pid_key = normalize_pid(pid)
        if not pid_key:
            return []
        should_wait = False
        query_source = "skip"
        need_cookie = None

        def _finalize(ret_value, wait_applied=False):
            return self._step3_finalize_query(ret_value, query_source, need_cookie, wait_applied)

        if pid_key in self.exist_pid:
            self._step3_safe_emit(f"<p><font color='gray'>PID {pid_key} 已存在於 exist_pid，URL階段自動跳過</font></p>")
            self._step3_advance_progress()
            return _finalize([])
        download_url=[]
        url='https://www.pixiv.net/artworks/'+pid_key
        # 先讀取本地快取，避免重複查詢 API
        cached = self.url_meta.get(pid_key) if isinstance(self.url_meta, dict) else None
        if isinstance(cached, dict) and cached.get('img_url') not in (None, 'None', '') and int(cached.get('pagecount', 0) or 0) > 0:
            self._pid_cache_hit[pid_key] = True
            query_source = "cache"
            tag, like, pagecount, img_url, need_cookie = self._step3_extract_meta_from_cache(cached)
            need_cookie = self._refresh_cookie_requirement(pid_key, fallback=need_cookie)
            if self.single_mode_flag and bool(getattr(self, "_log_step3_cache_detail", False)):
                self._step3_safe_emit(f"<p><font color='gray'>[URL階段] PID {pid_key} 使用本地快取，跳過等待</font></p>")
        else:
            self._pid_cache_hit[pid_key] = False
            should_wait = True
            query_source = "network"
            pid_cookie = self._select_cookie_for_pid(pid_key)
            self._record_cookie_usage("step3", pid_key, pid_cookie)
            try:
                if pid_cookie:
                    info = Pixiv_info(url, Agent=Agent, cookie=pid_cookie)
                else:
                    info = Pixiv_info(url, Agent=Agent)
            except Exception as e:
                self._step3_safe_emit(f"<p><font color='red'>PID {pid_key} 取得資訊失敗：{e}</font></p>")
                if should_wait:
                    self._sleep_ultra_slow(pid_key, need_cookie=None)
                self._step3_advance_progress()
                return _finalize([str(pid_key)], wait_applied=bool(should_wait))
            if info == [404]:
                self._mark_revoked_pid(pid_key, reason="404")
                if should_wait:
                    self._sleep_ultra_slow(pid_key, need_cookie=None)
                self._step3_advance_progress()
                return _finalize([str(pid_key)], wait_applied=bool(should_wait))
            try:
                tag,like,pagecount,img_url = info
            except Exception:
                if should_wait:
                    self._sleep_ultra_slow(pid_key, need_cookie=None)
                self._step3_advance_progress()
                return _finalize([str(pid_key)], wait_applied=bool(should_wait))
            fallback_req = self._step3_safe_cookie_requirement(pid_key)
            need_cookie = self._refresh_cookie_requirement(pid_key, fallback=fallback_req)

        query_source = "cache" if bool(self._pid_cache_hit.get(pid_key, False)) else "network"
        self._step3_build_url_meta_entry(
            pid_key, tag, like, pagecount, img_url, need_cookie, url, query_source,
        )

        passed, reason = self._passes_artwork_filters(pid_key, tag, like)
        self._step3_record_filter_result(pid_key, passed, reason)
        if not passed:
            if should_wait:
                self._sleep_ultra_slow(pid_key, need_cookie=need_cookie)
            self._step3_advance_progress()
            return _finalize([], wait_applied=bool(should_wait))

        if not img_url or str(img_url) == 'None':
            if should_wait:
                self._sleep_ultra_slow(pid_key, need_cookie=need_cookie)
            self._step3_advance_progress()
            return _finalize([str(pid_key)], wait_applied=bool(should_wait))
        built_urls = self._step3_build_pid_download_urls(img_url, pagecount)
        if built_urls is None:
            if should_wait:
                self._sleep_ultra_slow(pid_key, need_cookie=need_cookie)
            self._step3_advance_progress()
            return _finalize([str(pid_key)], wait_applied=bool(should_wait))
        download_url.extend(built_urls)
        if should_wait:
            self._sleep_ultra_slow(pid_key, need_cookie=need_cookie)
        self._step3_advance_progress()

        # for x in range(0,2):
        #     try:
        #         #print('瑼Ｘ葫tag')
        #         url='https://www.pixiv.net/artworks/'+pid
        #         #print(url)
        #         j=1
        #         while(j < 3):
        #             tag,like,pagecount,img_url=Pixiv_info(url,Agent=Agent,cookie=self.cookies)
        #             print(img_url)
        #             j = j + 1
        #             if tag != [] or like != 404:
        #                 break
        #             if tag == 404 and like == 404:
        #                 break
        #         if j == 3:
        #             raise Exception()
        #         if tag ==404 and like==404:
        #             break
        #         tag=str(tag) 
        #         self.ban_tag=tag_edit.Tag(self.ban_tag)
        #         for i in self.ban_tag:
        #             if i in tag:
        #                 info="<p><font color='black'>因封鎖標籤 {}，已略過 TAG PID：{}</font></p>"
        #                 self._q.put(WorkerEvent("output", info.format(i, pid)))
        #                 self.tag_queue.put(pid)
        #                 return ['0']
        #         self.must_tag=tag_edit.Tag(self.must_tag)
        #         if self.must_tag!=[]:
        #             ok_status=0
        #             for i in self.must_tag:
        #                 if i in tag:   
        #                     ok_status=1
        #                     break
        #             if ok_status==0:
        #                 self._q.put(WorkerEvent("output", "<p><font color='black'>TAG 過濾略過 PID：" + pid + "</font></p>"))
        #                 self.tag_queue.put(pid)
        #                 return ['0']
        #         if like <self.like_num:
        #             #print('讚數不足：' + str(like) + '，略過 PID：' + pid)
        #             self._q.put(WorkerEvent("output", "<p><font color='black'>讚數不足："+str(like)+"，略過 PID："+pid+"</font></p>"))
        #             if int(pid)<94006000:
        #                 self.like_queue.put(pid)
        #             return ['0']     
        #         img_url=img_url.rsplit(".",1)
        #         for count in range(0,pagecount):
        #             download_url.append(img_url[0]+str(count)+"."+img_url[1])
        #         time.sleep(random()/5)
        #         return (download_url)   
        #     except Exception as err:
        #         output_err(err)
        #         print(pid+' 下載失敗', err)
        #         if x==9:
        #                 print(pid+' 連續重試 9 次仍失敗', err)
        #                 myfile = Path(path+"network_err"+str(num%20)+".txt")
        #                 myfile.touch(exist_ok=True)
        #                 f = open((path+"network_err"+str(num%20)+".txt"), "r")           
        #                 exist=f.read()
        #                 f.close()
        #                 if str(pid) not in exist:
        #                     f = open((path+"network_err"+str(num%20)+".txt"), "a+")  
        #                     f.write(str(pid)+'\n')
        #                     f.close() 
            #time.sleep(0.5+random()/10)
        return _finalize(download_url, wait_applied=bool(should_wait))

