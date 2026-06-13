"""Tag/like filter decision + skip/query bookkeeping for ``get_img_url_thread``.

The artwork filter pipeline (``_passes_artwork_filters`` and its ban-tag /
must-tag / like-threshold predicates), the tag-normalize helpers, and the
Step 3 skip-count / query-count counters and their summary emitters — mixed
into ``get_img_url_thread`` via ``_Step3FiltersMixin``. Every method uses only
``self.`` for cross-method calls (resolved through inheritance) plus the
module-level names imported below, so behavior is byte-for-byte identical to
the originals. Helpers these methods call but do not own (``self._to_int``,
``self._diag``, ``self.tag_queue`` / ``self.like_queue`` via ``getattr``) live
on the concrete class.
"""
from __future__ import annotations

from app.core.pixiv_thread_base import _resolve_like_threshold
from app.core.pixiv_thread_utils import normalize_filter_tags
from app.core.worker_event import WorkerEvent


class _Step3FiltersMixin:
    """Tag/like filter pipeline + Step 3 skip/query bookkeeping."""

    def _normalize_filter_tags(self, tags):
        return normalize_filter_tags(tags)

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

    def _bump_step3_skip_count(self, reason):
        """Increment a counter, creating the key on first sight."""
        try:
            key = str(reason or "other")
            self._step3_filter_skip_counts.setdefault(key, 0)
            self._step3_filter_skip_counts[key] += 1
            return key
        except Exception:
            return str(reason or "other")

    def _step3_skip_total(self):
        """Sum of all step3 filter skip counters."""
        try:
            return int(sum(int(v or 0) for v in self._step3_filter_skip_counts.values()))
        except Exception:
            return 0

    def _maybe_emit_step3_skip_notice(self):
        """Emit the one-time '已啟用精簡輸出' notice the first time we filter."""
        if self._step3_filter_skip_notice_emitted:
            return
        self._step3_filter_skip_notice_emitted = True
        try:
            self._q.put(WorkerEvent("output",
                "<p><font color='gray'>[Step3過濾] 已啟用精簡輸出；詳細 PID 可查看 "
                "tag_ban_pid.txt 與 pid_num_pid.txt</font></p>"
            ))
        except Exception:
            pass

    def _maybe_emit_step3_skip_summary(self, total):
        """Emit a summary every Nth skip."""
        try:
            if total > 0 and total % int(self._step3_filter_skip_every) == 0:
                self._q.put(WorkerEvent("output",
                    "<p><font color='gray'>[Step3過濾摘要] 已略過 {} 筆"
                    "（標籤={}、必含標籤={}、低愛心={}）</font></p>".format(
                        total,
                        int(self._step3_filter_skip_counts.get("ban_tag", 0)),
                        int(self._step3_filter_skip_counts.get("must_tag", 0)),
                        int(self._step3_filter_skip_counts.get("like", 0)),
                    )
                ))
        except Exception:
            pass

    def _record_step3_filter_skip(self, reason, pid_key=None):
        key = self._bump_step3_skip_count(reason)
        try:
            self._diag("step3_filter_skip", reason=key, pid=str(pid_key or ""))
        except Exception:
            pass
        self._maybe_emit_step3_skip_notice()
        self._maybe_emit_step3_skip_summary(self._step3_skip_total())

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

    def _bump_step3_query_source(self, query_source):
        """Increment the per-source query counter (network/cache/skip)."""
        source = str(query_source or "skip").strip().lower()
        if source not in self._step3_query_counts:
            source = "skip"
        try:
            self._step3_query_counts[source] += 1
        except Exception:
            pass

    def _bump_step3_cookie_requirement(self, need_cookie):
        """Increment the per-need_cookie counter (need/free/unknown)."""
        if need_cookie is True:
            key = "need"
        elif need_cookie is False:
            key = "free"
        else:
            key = "unknown"
        try:
            self._step3_cookie_req_counts[key] += 1
        except Exception:
            pass

    def _maybe_emit_step3_progress(self):
        """Emit a non-final summary every Nth query, if the total is past zero."""
        try:
            total = int(sum(int(v or 0) for v in self._step3_query_counts.values()))
        except Exception:
            return
        try:
            if total > 0 and total % int(self._step3_query_notice_every) == 0:
                self._emit_step3_query_final_summary(final=False)
        except Exception:
            pass

    def _record_step3_query_result(self, query_source, need_cookie=None, wait_applied=False):
        self._bump_step3_query_source(query_source)
        self._bump_step3_cookie_requirement(need_cookie)
        if wait_applied:
            try:
                self._step3_wait_applied_count += 1
            except Exception:
                pass
        self._maybe_emit_step3_progress()

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

    def _record_filter_rejection(self, pid_key, reason, queue_attr):
        """Append the PID to the named queue and record a step3 skip event."""
        try:
            getattr(self, queue_attr).put(str(pid_key))
        except Exception:
            pass
        self._record_step3_filter_skip(reason, pid_key=pid_key)

    def _step3_blocked_by_ban_tag(self, artwork_tags):
        return any(self._tag_hit(blocked, artwork_tags) for blocked in self._ban_tag_norm)

    def _step3_missing_must_tag(self, artwork_tags):
        if not self._must_tag_norm:
            return False
        return not any(self._tag_hit(req, artwork_tags) for req in self._must_tag_norm)

    def _step3_below_like_threshold(self, artwork_tags, like):
        like_limit, _ = _resolve_like_threshold(
            self.like_num, artwork_tags, self.special_like_rules,
            self._tag_hit, self._to_int,
        )
        like_value = self._to_int(like, None)
        return like_limit > 0 and like_value is not None and like_value < like_limit

    def _passes_artwork_filters(self, pid_key, tag, like):
        artwork_tags = self._normalize_artwork_tags(tag)
        if self._step3_blocked_by_ban_tag(artwork_tags):
            self._record_filter_rejection(pid_key, "ban_tag", "tag_queue")
            return False, "ban_tag"
        if self._step3_missing_must_tag(artwork_tags):
            self._record_filter_rejection(pid_key, "must_tag", "tag_queue")
            return False, "must_tag"
        if self._step3_below_like_threshold(artwork_tags, like):
            self._record_filter_rejection(pid_key, "like", "like_queue")
            return False, "like"
        return True, "pass"
