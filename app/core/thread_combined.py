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
        )

        # Share one set of control events + one DB connection.
        self.fetcher._pause_event = self._pause_event
        self.fetcher._stop_event = self._stop_event
        self.downloader._pause_event = self._pause_event
        self.downloader._stop_event = self._stop_event
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
        db = self.fetcher._metadata_db
        try:
            pending = db.pids_with_pending_pages() if db is not None else []
        except Exception:
            pending = []
        download_only = [
            normalize_pid(p) or str(p)
            for p in pending
            if (normalize_pid(p) or str(p)) not in query_set
        ]
        return query_pids, download_only

    def _download_only_urls(self, pid):
        """Per-page pending URLs for a download-only PID, from the DB."""
        db = self.fetcher._metadata_db
        if db is None:
            return []
        pid_key = normalize_pid(pid) or str(pid)
        try:
            rows = db.get_pending_pages()  # [(pid, page_index, url)]
        except Exception:
            return []
        urls = [
            str(u) for (p, _idx, u) in rows
            if (normalize_pid(p) or str(p)) == pid_key and u
        ]
        return urls

    def _process_one_pid(self, pid, needs_query):
        """Acquire one account; query (if needed) + download this PID's pages;
        release. Returns the per-PID failed list (possibly empty)."""
        acc = self._acquire_account()
        if acc is None:
            return None  # stop signal / all disabled -> caller breaks
        sess = pixiv_api.make_session(acc.proxy_url)
        failed = []
        ok = True
        try:
            if needs_query:
                ok, one, _ = self._run_with_network_retry(
                    f"PID {pid}",
                    lambda: self.fetcher.get_download_url(
                        self.path, self.Agent, 1, pid,
                        cookie_override=acc.cookie, session=sess,
                    ),
                )
                urls = self.fetcher._normalize_loop_result(one)
            else:
                urls = self._download_only_urls(pid)

            urls = [u for u in urls if isinstance(u, str) and u.startswith("http")]
            if urls:
                self.downloader._current_account = acc
                failed = self.downloader._download_pid_group(pid, urls)
                if not failed:
                    self.downloader._maybe_flush_exist_pid(pid)
                self.downloader._current_account = None
        finally:
            self._release_account(acc, ok=ok)
            with contextlib.suppress(Exception):
                sess.close()
        return failed if isinstance(failed, list) else []

    def _emit(self, html):
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output", html))

    def run(self):
        try:
            self._share_scheduler()
            self._emit("<p><font color='red'>邊查邊下階段開始</font></p>")
            self.fetcher._reset_run_counters()
            query_pids, download_only = self._build_work_lists()
            total = len(query_pids) + len(download_only)
            self._q.put(WorkerEvent("progress", (0, total)))
            self._emit(
                f"<p><font color='red'>待處理：查詢+下載 {len(query_pids)} 筆、"
                f"純下載(吸收上次未完成) {len(download_only)} 筆</font></p>"
            )

            failed_nested = []
            for pid, needs_query in (
                [(p, True) for p in query_pids] + [(p, False) for p in download_only]
            ):
                if self._stop_event.is_set():
                    break
                failed = self._process_one_pid(pid, needs_query)
                if failed is None:  # acquire returned stop / all disabled
                    break
                failed_nested.append(failed)
                if needs_query:
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
        # Downloader side: err_url + DB completion marks.
        with contextlib.suppress(Exception):
            self.downloader._finalize_downloads(failed_nested)
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
