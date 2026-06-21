import contextlib
import os

from app.core.worker_event import WorkerEvent
from app.core.pixiv_thread_base import PauseableThread
from app.core.pixiv_thread_utils import normalize_pid
from app.core import thread_url_fetch, thread_download, diag_log
from app.core.combined_progress_queues import (
    _CombinedPageProgressQueue,
    _DropOverallProgressQueue,
)
from app.core.combined_work_lists import _CombinedWorkListsMixin
import pixiv_api


class combined_thread(PauseableThread, _CombinedWorkListsMixin):
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
        set_file_mtime=True,
        download_deadline_sec=None,
        special_like_rules=None,
        rescrape_within_days=365,
        scheduler=None,
        stats_collector=None,
        event_log=None,
        live=None,
        r18_like_num=0,
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
            live=live,
            r18_like_num=r18_like_num,
        )
        self.downloader = thread_download.download_thread(
            q=q, nogif=nogif, notag=notag, notime=notime, create_dir=create_dir,
            download_path=download_path or self.path, cookies=cookies, agent=Agent,
            download_time=download_time, no_R18G_dir=no_R18G_dir, no_R18_dir=no_R18_dir,
            single_thread_mode=single_thread_mode,
            intra_pid_wait_min=intra_pid_wait_min, intra_pid_wait_max=intra_pid_wait_max,
            jxl_enable=jxl_enable, jxl_cjxl_path=jxl_cjxl_path,
            jxl_delete_original=jxl_delete_original, jxl_effort=jxl_effort,
            like_num=like_num, r18_like_num=r18_like_num, ban_tag=ban_tag, must_tag=must_tag,
            special_like_rules=special_like_rules or [], ai_gen_dir=ai_gen_dir,
            filename_template=filename_template,
            tag_strip_brackets=tag_strip_brackets,
            tag_strip_special_chars=tag_strip_special_chars,
            author_order=author_order, set_file_mtime=set_file_mtime,
            download_deadline_sec=download_deadline_sec,
            stats_collector=stats_collector,
            event_log=event_log,
            defer_step4_scan=True, db_base_path=self.path,
            live=live,
        )

        # Share one set of control events + one DB connection.
        self.fetcher._pause_event = self._pause_event
        self.fetcher._stop_event = self._stop_event
        self.downloader._pause_event = self._pause_event
        self.downloader._stop_event = self._stop_event
        with contextlib.suppress(Exception):
            self.downloader._metadata_db.close()
        self.downloader._metadata_db = self.fetcher._metadata_db
        # Bridge the just-queried meta to the download leg. combined runs each
        # PID sequentially (query then download), so the fetcher's url_meta —
        # which get_download_url populates during the query — is exactly what the
        # downloader's _get_meta needs. Aliasing the dict means the download leg
        # reads that meta in-memory instead of re-issuing a redundant,
        # un-cooldowned, un-proxied Pixiv_info network fetch per page.
        self.downloader.url_meta = self.fetcher.url_meta

    def _share_scheduler(self):
        """Propagate the scheduler set by run_actions after construction."""
        self.fetcher._scheduler = self._scheduler
        self.downloader._scheduler = self._scheduler

    def _process_one_pid(self, pid, needs_query):
        """Acquire one account; query (if needed) + download this PID's pages;
        release. Returns the per-PID failed list (possibly empty).

        Side effect: sets ``self._last_pid_ok`` to whether this PID was
        processed cleanly (query ok / not needed, AND every page downloaded).
        ``run()`` reads it to gate ``_mark_pid_processed`` so an exhausted
        query or a partial-download failure is NOT silently dropped from the
        pending list.
        """
        # Pick up any mid-run 「儲存設定」 on both engines before this PID
        # (one shared LiveSettings; the signature check makes this near-free).
        self.fetcher._apply_live_settings_if_changed()
        self.downloader._apply_live_settings_if_changed()
        self._last_pid_ok = False
        # Phase BEFORE acquire: the cooldown wait happens inside
        # _acquire_account, so emitting here lets the UI show
        # 「正在查詢：PID x」(or 正在下載) alongside the 倒數 ticks instead of
        # leaving the previous PID's label on screen during the wait.
        with contextlib.suppress(Exception):
            verb = "正在查詢" if needs_query else "正在下載"
            self._q.put(WorkerEvent("phase", f"{verb}：PID {pid}"))
        diag_log.log(diag_log.WORKER, f"PID {pid} 開始 (needs_query={needs_query})")
        # The acquire span's elapsed time IS the visible cooldown wait. When it
        # logs ~0.00s the account was ready immediately (cooldown absorbed by the
        # previous PID's download) — that is exactly why no 倒數 shows.
        with diag_log.span(diag_log.WORKER, f"PID {pid} acquire(cooldown)"):
            acc = self._acquire_account()
        if acc is None:
            diag_log.log(diag_log.WORKER, f"PID {pid} acquire -> None (stop/all-disabled)")
            return None  # stop signal / all disabled -> caller breaks
        diag_log.log(diag_log.WORKER, f"PID {pid} 取得帳號 alias={getattr(acc, 'alias', '?')}")
        sess = pixiv_api.make_session(acc.proxy_url)
        failed = []
        ok = True
        account_ok = True
        download_ok = True
        neutral = False
        try:
            if needs_query:
                # The fetcher's get_download_url emits one ("progress",(1,
                # fetcher.pid_max)) per PID via _step3_advance_progress, and
                # fetcher.pid_max is 0 in combined mode (run() never ran). That
                # (1, 0) would blank/hide the 整體進度 bar on every query — the
                # "bar disappears when the next PID starts" bug. combined owns
                # overall progress (one tick per PID in run()), so drop the
                # fetcher's progress events for the duration of the query.
                # (The 「正在查詢」 phase event was already emitted before
                # _acquire_account so the countdown wait shows it.)
                prev_fetcher_q = self.fetcher._q
                self.fetcher._q = _DropOverallProgressQueue(prev_fetcher_q)
                try:
                    with diag_log.span(diag_log.WORKER, f"PID {pid} query"):
                        ok, one, _ = self._run_with_network_retry(
                            f"PID {pid}",
                            lambda: self.fetcher.get_download_url(
                                self.path, self.Agent, 1, pid,
                                cookie_override=acc.cookie, session=sess,
                            ),
                        )
                finally:
                    self.fetcher._q = prev_fetcher_q
                urls = self.fetcher._normalize_loop_result(one) if ok else []
            else:
                urls = self._download_only_urls(pid)

            urls = [u for u in urls if isinstance(u, str) and u.startswith("http")]
            if urls:
                # Seed the pending pages before download so a partial failure /
                # crash leaves a recoverable pending trail in the DB.
                self._seed_pending_urls(pid, urls)
                with contextlib.suppress(Exception):
                    self._q.put(WorkerEvent("phase", f"正在下載：PID {pid}"))
                self.downloader._set_current_download_account(acc)
                # Bind THIS account's cookie to the PID so the download leg sends
                # the held account's cookie over the held account's proxy (the
                # hard cookie<->IP contract). Without this seed,
                # _select_cookie_for_pid would pick a RANDOM pool cookie and send
                # it over acc's IP — a mismatch Pixiv anti-fraud flags. Mirrors
                # standalone Step 4 (_download_pid_with_scheduler).
                self.downloader._pid_cookie_selection[normalize_pid(pid) or str(pid)] = acc.cookie
                with diag_log.span(diag_log.WORKER, f"PID {pid} download ({len(urls)} pages)"):
                    account_ok, failed, _ = self._run_with_network_retry(
                        f"PID {pid} 下載",
                        lambda: self._download_pid_group_with_page_progress(pid, urls),
                    )
                if not isinstance(failed, list):
                    failed = []
                    download_ok = False
                elif failed:
                    download_ok = False
                elif self._stop_event.is_set():
                    # Stopped mid-PID. _download_pid_group only reports
                    # *attempted* failures, so the pages that were never
                    # reached are absent from `failed` (it is []) — which must
                    # NOT be read as "all pages done". Mark the PID incomplete
                    # so its seeded pending rows + pictures_id.txt entry survive
                    # and the next run resumes the remaining pages instead of
                    # skipping the whole PID as already downloaded.
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
            else:
                self._clear_page_progress()
        except Exception:
            neutral = True  # non-network failure: not the cookie's fault
            raise
        finally:
            self.downloader._clear_current_download_account()
            # Stop mid-PID or a non-network error releases NEUTRALLY: don't credit
            # the cookie with a success (ok=True would refresh its trust window for
            # work the user aborted) nor disable it. Off those paths, a
            # network-exhausted account (account_ok False) still disables as before.
            self._release_account_after_work(acc, ok=ok and account_ok, neutral=neutral)
            with contextlib.suppress(Exception):
                sess.close()
        # "Genuine success": query succeeded (or not needed) AND downloads
        # were clean. A query that produced no urls (revoked / filtered out)
        # leaves ``download_ok`` True and is correctly settled. Only a failed/
        # exhausted query or a partial download keeps the PID pending.
        self._last_pid_ok = bool(ok) and download_ok
        return failed if isinstance(failed, list) else []

    def _download_pid_group_with_page_progress(self, pid, urls):
        page_q = _CombinedPageProgressQueue(self._q, pid, len(urls))
        original_q = self.downloader._q
        self.downloader._q = page_q
        try:
            page_q.reset()
            return self.downloader._download_pid_group(pid, urls)
        finally:
            self.downloader._q = original_q

    def _clear_page_progress(self):
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("page_progress", None))

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
                # Persist the advanced timetag counter after every PID —
                # combined never runs Step 4's finalize, so without this the
                # next run reuses the same download_time start and stamps
                # duplicate filename prefixes.
                with contextlib.suppress(Exception):
                    self.downloader._emit_timechanged()

            self._finalize(failed_nested)
        except Exception as e:
            from app.core.pixiv_thread_utils import output_err
            self._emit("<p><font color='red'>邊查邊下失敗</font></p>")
            self._emit(output_err(e))
            self._q.put(WorkerEvent("next", -1))

    def _finalize(self, failed_nested):
        # Fetcher side: persist meta + revoked + pending list.
        with contextlib.suppress(Exception):
            self.fetcher._flush_url_meta_snapshot(full=True)
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
            self.fetcher._flush_url_meta_snapshot(full=True)
        db = getattr(self.fetcher, "_metadata_db", None)
        if db is not None:
            with contextlib.suppress(Exception):
                db.close()
