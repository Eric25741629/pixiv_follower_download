import contextlib
import os

from app.core.worker_event import WorkerEvent
from app.core.pixiv_thread_base import PauseableThread
from app.core.pixiv_thread_utils import normalize_pid
from app.core import thread_url_fetch, thread_download
import pixiv_api


class combined_thread(PauseableThread):
    """邊查邊下: per PID, query meta (Step 3) then immediately download
    its pages (Step 4) inside one account cooldown window.

    Composes a get_img_url_thread (query engine) and a download_thread
    (download engine) as helpers; never calls their run(). Shares one
    event queue, scheduler, pause/stop events, and metadata DB.
    """

    path = os.getenv("APPDATA") + r"/pixiv_download/"

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
        *,
        download_path=None,
        download_time=None,
        nogif=False,
        notag=False,
        notime=False,
        create_dir=False,
        no_R18G_dir=False,
        no_R18_dir=False,
        intra_pid_wait_min=5,
        intra_pid_wait_max=15,
        pid_wait_nocookie_min=1,
        pid_wait_nocookie_max=6,
        jxl_enable=False,
        jxl_cjxl_path="",
        jxl_delete_original=False,
        jxl_effort=7,
        ai_gen_dir=False,
        filename_template="",
        tag_strip_brackets=False,
        tag_strip_special_chars=False,
        author_order=False,
        special_like_rules=None,
        rescrape_within_days=365,
        scheduler=None,
        stats_collector=None,
        event_log=None,
    ):
        super().__init__(q, scheduler=scheduler)
        if isinstance(base_path, str) and base_path.strip():
            self.path = base_path
        self.Agent = Agent
        self._event_log = event_log
        self._pending_urls_by_pid = {}
        self._last_pid_ok = False
        self.author_order = bool(author_order)

        self.fetcher = thread_url_fetch.get_img_url_thread(
            q=q, Author_list=Author_list, Agent=Agent, cookies=cookies,
            exist_pid=exist_pid, ban_tag=ban_tag, must_tag=must_tag,
            like_num=like_num, no_to_check=no_to_check, base_path=self.path,
            single_thread_mode=single_thread_mode,
            pid_wait_nocookie_min=pid_wait_nocookie_min,
            pid_wait_nocookie_max=pid_wait_nocookie_max,
            special_like_rules=special_like_rules or [],
            stats_collector=stats_collector, event_log=event_log,
            rescrape_within_days=rescrape_within_days,
        )
        self.downloader = thread_download.download_thread(
            q=q, nogif=nogif, notag=notag, notime=notime, create_dir=create_dir,
            download_path=download_path or self.path, cookies=cookies, agent=Agent,
            download_time=download_time, no_R18G_dir=no_R18G_dir, no_R18_dir=no_R18_dir,
            single_thread_mode=single_thread_mode,
            intra_pid_wait_min=intra_pid_wait_min, intra_pid_wait_max=intra_pid_wait_max,
            jxl_enable=jxl_enable, jxl_cjxl_path=jxl_cjxl_path,
            jxl_delete_original=jxl_delete_original, jxl_effort=jxl_effort,
            like_num=like_num, ban_tag=ban_tag, must_tag=must_tag,
            special_like_rules=special_like_rules or [], ai_gen_dir=ai_gen_dir,
            filename_template=filename_template,
            tag_strip_brackets=tag_strip_brackets,
            tag_strip_special_chars=tag_strip_special_chars,
            author_order=author_order, stats_collector=stats_collector,
            event_log=event_log,
            defer_step4_scan=True, db_base_path=self.path,
        )

        # Share one set of control events + one DB connection.
        self.fetcher._pause_event = self._pause_event
        self.fetcher._stop_event = self._stop_event
        self.downloader._pause_event = self._pause_event
        self.downloader._stop_event = self._stop_event
        with contextlib.suppress(Exception):
            self.downloader._metadata_db.close()
        self.downloader._metadata_db = self.fetcher._metadata_db

    def _share_scheduler(self):
        """Propagate the scheduler set by run_actions after construction."""
        self.fetcher._scheduler = self._scheduler
        self.downloader._scheduler = self._scheduler

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

    def _process_one_pid(self, pid, needs_query):
        """Acquire one account; query (if needed) + download this PID's pages;
        release. Returns the per-PID failed list (possibly empty).

        Side effect: sets ``self._last_pid_ok`` to whether this PID was
        processed cleanly (query ok / not needed, AND every page downloaded).
        ``run()`` reads it to gate ``_mark_pid_processed`` so an exhausted
        query or a partial-download failure is NOT silently dropped from the
        pending list.
        """
        self._last_pid_ok = False
        acc = self._acquire_account()
        if acc is None:
            return None  # stop signal / all disabled -> caller breaks
        sess = pixiv_api.make_session(acc.proxy_url)
        failed = []
        ok = True
        download_ok = True
        try:
            if needs_query:
                ok, one, _ = self._run_with_network_retry(
                    f"PID {pid}",
                    lambda: self.fetcher.get_download_url(
                        self.path, self.Agent, 1, pid,
                        cookie_override=acc.cookie, session=sess,
                    ),
                )
                urls = self.fetcher._normalize_loop_result(one) if ok else []
            else:
                urls = self._download_only_urls(pid)

            urls = [u for u in urls if isinstance(u, str) and u.startswith("http")]
            if urls:
                # Seed the pending pages before download so a partial failure /
                # crash leaves a recoverable pending trail in the DB.
                self._seed_pending_urls(pid, urls)
                self.downloader._current_account = acc
                _, failed, _ = self._run_with_network_retry(
                    f"PID {pid} 下載",
                    lambda: self.downloader._download_pid_group(pid, urls),
                )
                if not isinstance(failed, list):
                    failed = []
                    download_ok = False
                elif failed:
                    download_ok = False
                else:
                    self._mark_urls_done(urls)
                    self.downloader._maybe_flush_exist_pid(pid)
                    # A queried PID's meta lives only in the fetcher's
                    # in-memory url_meta until _finalize. Persist it now so
                    # page_count + meta_updated_at land immediately, making
                    # v_complete_artworks / v_closed_artworks exact and
                    # crash-safe. Download-only PIDs already have DB meta.
                    if needs_query:
                        self._persist_pid_meta(pid)
        finally:
            self.downloader._current_account = None
            self._release_account(acc, ok=ok and download_ok)
            with contextlib.suppress(Exception):
                sess.close()
        # "Genuine success": query succeeded (or not needed) AND downloads
        # were clean. A query that produced no urls (revoked / filtered out)
        # leaves ``download_ok`` True and is correctly settled. Only a failed/
        # exhausted query or a partial download keeps the PID pending.
        self._last_pid_ok = bool(ok) and download_ok
        return failed if isinstance(failed, list) else []

    def _seed_pending_urls(self, pid, urls):
        db = self.fetcher._metadata_db
        if db is None:
            return
        with contextlib.suppress(Exception):
            db.upsert_pending_urls([(u, pid) for u in urls])

    def _mark_urls_done(self, urls):
        db = self.fetcher._metadata_db
        if db is None:
            return
        with contextlib.suppress(Exception):
            db.mark_urls_done(list(urls))

    def _persist_pid_meta(self, pid):
        """Persist a queried PID's meta (page_count, etc.) to the DB now.

        Called only for needs_query clean-success PIDs so the canonical
        v_complete_artworks / v_closed_artworks views become exact at the
        moment of success instead of waiting for _finalize.
        """
        db = self.fetcher._metadata_db
        if db is None:
            return
        key = normalize_pid(pid) or str(pid)
        entry = (self.fetcher.url_meta or {}).get(key)
        if isinstance(entry, dict):
            with contextlib.suppress(Exception):
                db.import_meta_dict({key: entry})

    def _emit(self, html):
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output", html))

    def run(self):
        try:
            self._share_scheduler()
            self._emit("<p><font color='red'>邊查邊下階段開始</font></p>")
            self.fetcher._reset_run_counters()
            query_pids, download_only = self._build_work_lists()
            # Resolve the (optionally author-grouped) iteration order once so the
            # progress denominator always matches what we actually iterate.
            order = self._resolve_combined_order(query_pids, download_only)
            total = len(order)
            self._q.put(WorkerEvent("progress", (0, total)))
            self._emit(
                f"<p><font color='red'>待處理：查詢+下載 {len(query_pids)} 筆、"
                f"純下載(吸收上次未完成) {len(download_only)} 筆</font></p>"
            )

            failed_nested = []
            for pid, needs_query in order:
                if self._stop_event.is_set():
                    break
                failed = self._process_one_pid(pid, needs_query)
                if failed is None:  # acquire returned stop / all disabled
                    break
                failed_nested.append(failed)
                # Only retire a queried PID from the pending list when it was
                # processed cleanly; exhausted queries / partial downloads stay
                # pending so the next run retries them.
                if needs_query and self._last_pid_ok:
                    self.fetcher._mark_pid_processed(pid)
                self._q.put(WorkerEvent("progress", (1, total)))

            self._finalize(failed_nested)
        except Exception as e:
            from app.core.pixiv_thread_utils import output_err
            self._emit("<p><font color='red'>邊查邊下失敗</font></p>")
            self._emit(output_err(e))
            self._q.put(WorkerEvent("next", -1))

    def _finalize(self, failed_nested):
        # Fetcher side: persist meta + revoked + pending list.
        with contextlib.suppress(Exception):
            self.fetcher._flush_url_meta_snapshot()
            self.fetcher._persist_pending_pid_file()
            self.fetcher._flush_revoked_pid_file()
        # Downloader side: failures only. We do NOT call
        # _finalize_downloads — under defer_step4_scan downloader.allurl is []
        # so its all_url.txt rewrite would clobber the file, and its DB
        # completion marks are redundant (combined marks per-PID via
        # _mark_urls_done). Record err_url + shadow-mark failed pages only.
        with contextlib.suppress(Exception):
            fail_records = self.downloader._classify_download_results(failed_nested)
            if fail_records:
                from app.core.pixiv_thread_utils import atomic_write_text
                atomic_write_text(
                    self.downloader.path + "/err_url.txt",
                    [f"{u} {i}" for (u, i) in fail_records],
                    backup=False,
                )
                self.downloader._shadow_mark_failures(fail_records)
        self._emit("<p><font color='green'>邊查邊下完成</font></p>")
        self._q.put(WorkerEvent("finished", "邊查邊下完成"))
        self._q.put(WorkerEvent("next", -1))  # terminal: Run All stops here

    def flush_for_shutdown(self):
        with contextlib.suppress(Exception):
            self.fetcher._flush_url_meta_snapshot()
        db = getattr(self.fetcher, "_metadata_db", None)
        if db is not None:
            with contextlib.suppress(Exception):
                db.close()
