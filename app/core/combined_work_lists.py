"""Pure work-queue construction / ordering for ``combined_thread``.

``_CombinedWorkListsMixin`` holds the methods that build and order the combined
mode work list — ``_build_work_lists``, ``_resolve_combined_order``, and
``_download_only_urls`` — moved verbatim out of ``thread_combined``. Every method
uses only ``self.`` for cross-method state (``self.fetcher``, ``self.path``,
``self._pending_urls_by_pid``, ``self.author_order``) plus the module-level names
imported below, so behavior is byte-for-byte identical to the originals.

``thread_download`` is imported at module level (no cycle: ``thread_download``
does not import this module or ``thread_combined``); ``normalize_pid`` comes from
``pixiv_thread_utils``.
"""
import contextlib

from app.core import thread_download
from app.core.pixiv_thread_utils import normalize_pid


class _CombinedWorkListsMixin:
    """Work-queue construction + (optional author-grouped) ordering for combined mode."""

    def _build_work_lists(self):
        """Return ``(query_pids, download_only_pids)``.

        query_pids: from pictures_id.txt, minus exist/revoked/dupes — need
            query then download. (Reuses the fetcher's pure filter helpers,
            NOT _load_and_filter_pid_list, to avoid its next/progress emits.)
        download_only_pids: PIDs with pending pages in the DB that are not in
            query_pids — a partial Step 3 already resolved their meta but never
            downloaded them. Download-only, no re-query.
        """
        raw = self.fetcher.check_exist()
        if not isinstance(raw, list):
            raw = []
        query_pids, *_ = self.fetcher._prepare_pending_pid_tasks(raw)
        query_set = set(query_pids)
        # Seed the in-memory pending-PID tracker from query_pids so finalize's
        # _persist_pending_pid_file does not overwrite pictures_id.txt empty.
        with contextlib.suppress(Exception):
            self.fetcher._init_pending_pid_tracker(
                query_pids, reset_with_fallback=True
            )
        # One full scan of v_pending_pages, grouped per PID, reused below
        # (avoids the previous O(D x P) re-scan per download-only PID).
        db = self.fetcher._metadata_db
        self._pending_urls_by_pid = {}
        try:
            rows = db.get_pending_pages() if db is not None else []
        except Exception:
            rows = []
        for (p, _idx, u) in rows:
            if not u:
                continue
            key = normalize_pid(p) or str(p)
            self._pending_urls_by_pid.setdefault(key, []).append(str(u))
        download_only = [
            key
            for key in self._pending_urls_by_pid
            if key not in query_set
        ]
        return query_pids, download_only

    def _resolve_combined_order(self, query_pids, download_only):
        """Return ``[(pid, needs_query), ...]`` for :meth:`run` to iterate.

        author_order off -> query batch then download-only batch (unchanged,
            zero regression).
        author_order on  -> both batches merged, deduped by normalized pid
            (the query batch is prepended so a query pid wins over a
            download-only dup), then grouped so each author's works are
            contiguous via :func:`thread_download.compute_author_order`;
            author-unknown pids bucket last (mirrors Step 4). combined mode is
            inherently per-PID sequential (one account per PID), so reordering
            the flat list is enough — no per-author barrier needed.

        Falls back to the unchanged order when no metadata DB is available or
        the user_id lookup fails.
        """
        pairs = [(p, True) for p in query_pids] + [(p, False) for p in download_only]
        if not getattr(self, "author_order", False):
            return pairs
        db = getattr(getattr(self, "fetcher", None), "_metadata_db", None)
        if db is None:
            return pairs
        needs_by_pid = {}
        ordered_pids = []
        seen = set()
        for pid, needs in pairs:
            key = normalize_pid(pid) or str(pid)
            if key in seen:
                continue
            seen.add(key)
            ordered_pids.append(pid)
            needs_by_pid[pid] = needs
        try:
            uid_map = db.user_id_map_for_pids(ordered_pids)
        except Exception:
            return pairs
        flat, _ = thread_download.compute_author_order(ordered_pids, uid_map)
        unknown = sum(1 for p in ordered_pids if not str(uid_map.get(p) or "").strip())
        if unknown:
            self._emit(
                f"<p><font color='orange'>[作者排序] {unknown} 筆作品作者不明，"
                f"已排到最後；重跑步驟 2/3 可補作者資料</font></p>"
            )
        return [(p, needs_by_pid[p]) for p in flat]

    def _download_only_urls(self, pid):
        """Per-page pending URLs for a download-only PID (from the cached
        per-PID grouping built once in :meth:`_build_work_lists`)."""
        pid_key = normalize_pid(pid) or str(pid)
        return list(getattr(self, "_pending_urls_by_pid", {}).get(pid_key, []))
