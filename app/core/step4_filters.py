"""Per-PID/URL filter subsystem for ``download_thread`` (file-size refactor).

The tag/like/AI/R-18 classification, the meta-for-filter fetch fallbacks, the
``_passes_pid_filter`` decision pipeline, the task-preparation pass over the
URL list, and the skip-count bookkeeping — mixed into ``download_thread`` via
``_Step4FiltersMixin``. Every method uses only ``self.`` for cross-method calls
(resolved through inheritance) plus the module-level names imported below, so
behavior is byte-for-byte identical to the originals. Helpers these methods
call but do not own (``_emit_output``, ``_diag``, ``_get_meta``, ``_to_int``,
``_normalize_filter_tags``, ``_acquire_account``, ``_release_account``,
``_run_with_network_retry``, ``_record_cookie_usage``, ``_select_cookie_for_pid``)
live on the concrete class or its base.
"""
from __future__ import annotations

import contextlib
import datetime
import os

import pixiv_api

from app.core.pixiv_thread_base import (
    _is_ai_artwork_tagged,
    _resolve_like_threshold,
)
from app.core.pixiv_thread_utils import normalize_pid
from app.core.worker_event import WorkerEvent


class _Step4FiltersMixin:
    """Tag/like filter + task-prep + skip bookkeeping, mixed into ``download_thread``."""

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
        return any(key in tag for tag in artwork_tags)

    def _is_r18g_artwork(self, tag):
        """Gore-adjacent adult content: r-18g / 糞 / 子宮脫."""
        artwork_tags = self._normalize_artwork_tags(tag)
        return any(self._tag_hit(marker, artwork_tags) for marker in ("r-18g", "糞", "子宮脫"))

    def _is_r18_artwork(self, tag):
        """General adult content (r-18) excluding r-18g markers."""
        if self._is_r18g_artwork(tag):
            return False
        artwork_tags = self._normalize_artwork_tags(tag)
        return any(t == "r-18" for t in artwork_tags)

    def _is_ai_artwork(self, tag):
        artwork_tags = self._normalize_artwork_tags(tag)
        return _is_ai_artwork_tagged(artwork_tags, self._tag_hit)

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
        with contextlib.suppress(Exception):
            self._diag("step4_filter_skip", reason=key, pid=str(pid_key or ""))
        self._maybe_emit_step4_skip_notice()
        self._maybe_emit_step4_skip_summary(self._step4_skip_total())

    def _emit_step4_filter_skip_final_summary(self):
        try:
            total = int(sum(int(v or 0) for v in self._step4_filter_skip_counts.values()))
        except Exception:
            total = 0
        if total <= 0:
            return
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output",
                "<p><font color='gray'>[Step4過濾完成] 共略過 {} 筆（標籤={}、低愛心={}、無meta={}）</font></p>".format(
                    total,
                    int(self._step4_filter_skip_counts.get("tag", 0)),
                    int(self._step4_filter_skip_counts.get("like", 0)),
                    int(self._step4_filter_skip_counts.get("no_meta", 0)),
                )
            ))

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
                with contextlib.suppress(Exception):
                    db.mark_artwork_revoked(pid_key)
        normalized = self._normalize_pixiv_info(info)
        if not normalized:
            return None
        tag, like, pagecount, img_url = normalized
        meta = self._build_filter_meta_entry(url, tag, like, pagecount, img_url, need_cookie)
        with contextlib.suppress(Exception):
            self.url_meta[pid_key] = meta
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
            self._r18_aware_like_base(artwork_tags),
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
            with contextlib.suppress(Exception):
                self._q.put(WorkerEvent("output",
                    f"<p><font color='orange'>[補meta] {added} 個缺 meta 的 PID "
                    f"已加回 pictures_id.txt（共 {len(merged)} 筆待辦），"
                    f"請再跑一次步驟 3 補抓資料</font></p>"
                ))
        except Exception:
            pass
