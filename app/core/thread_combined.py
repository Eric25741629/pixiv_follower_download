import concurrent.futures
import contextlib
import os
import threading

from app.core.worker_event import WorkerEvent
from app.core.pixiv_thread_base import PauseableThread
from app.core.pixiv_thread_utils import normalize_pid
from app.core import thread_url_fetch, thread_download, diag_log
from app.core.combined_progress_queues import (
    _CombinedPageProgressQueue,
    _DropOverallProgressQueue,
    _DropProgressQueue,
)
from app.core.combined_work_lists import _CombinedWorkListsMixin
import pixiv_api


# Hard ceiling on 邊查邊下 concurrency regardless of cookie count, so a huge
# pool can't spawn an unbounded thread fleet.
COMBINED_WORKERS_CAP = 16


def resolve_worker_count(setting, active_accounts, pending_count, cap=COMBINED_WORKERS_CAP):
    """Effective concurrency for combined mode.

    The user's ``setting`` is the cap they asked for; the real worker count is
    further bounded by the number of usable accounts (one cookie/proxy per
    concurrent PID is the hard contract), the pending work, and ``cap``. Always
    at least 1. ``setting <= 1`` -> 1 (sequential, zero regression).
    """
    def _int(v, default):
        try:
            return int(v)
        except Exception:
            return default
    s = _int(setting, 1)
    if s <= 1:
        return 1
    bounds = [s, _int(active_accounts, 1), _int(pending_count, 1), _int(cap, 1)]
    return max(1, min(bounds))


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
        jxl_skip_gif=True,
        ai_gen_dir=False,
        filename_template="",
        tag_strip_brackets=False,
        tag_strip_special_chars=False,
        author_order=False,
        combined_workers=1,
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
        try:
            self.combined_workers = max(1, int(combined_workers))
        except Exception:
            self.combined_workers = 1
        # Concurrent-mode bookkeeping (only used when combined_workers > 1).
        self._active_lock = threading.Lock()
        self._active_pids = {}

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
            jxl_skip_gif=jxl_skip_gif,
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
        """Sequential per-PID path (byte-identical to the pre-concurrency code).

        Thin wrapper over :meth:`_process_one_pid_core` with the sequential
        flags. Returns the per-PID failed list, or ``None`` when acquire
        returned no account (stop / all disabled). Sets ``self._last_pid_ok``
        for ``run()`` to gate ``_mark_pid_processed``.
        """
        failed, ok = self._process_one_pid_core(
            pid, needs_query,
            emit_phase=True, page_progress=True, drop_overall_inline=True,
            apply_live=True, reserve_timetag_block=False,
        )
        if failed is None:
            return None
        self._last_pid_ok = ok
        return failed

    def _process_one_pid_core(self, pid, needs_query, *, emit_phase,
                              page_progress, drop_overall_inline, apply_live,
                              reserve_timetag_block):
        """Core query+download for one PID. Returns ``(failed, ok)``.

        ``failed is None`` signals acquire returned no account (stop / all
        disabled). The flags let the concurrent path suppress per-PID phase /
        page-progress events (the coordinator owns those), skip the per-call
        queue swaps (the engines' queues are swapped once for the whole
        concurrent phase), skip the per-PID live-settings re-apply (done once
        up front), and reserve a contiguous timetag block so a PID's pages stay
        non-interleaved across concurrent workers.
        """
        if apply_live:
            # Pick up any mid-run 「儲存設定」 on both engines before this PID
            # (one shared LiveSettings; the signature check makes this near-free).
            self.fetcher._apply_live_settings_if_changed()
            self.downloader._apply_live_settings_if_changed()
        if emit_phase:
            # Phase BEFORE acquire so the UI shows the PID during the cooldown
            # wait instead of leaving the previous PID's label on screen.
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
            return None, False  # stop / all disabled -> caller breaks
        diag_log.log(diag_log.WORKER, f"PID {pid} 取得帳號 alias={getattr(acc, 'alias', '?')}")
        sess = pixiv_api.make_session(acc.proxy_url)
        failed = []
        ok = True
        account_ok = True
        download_ok = True
        neutral = False
        block_begun = False
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
                if drop_overall_inline:
                    prev_fetcher_q = self.fetcher._q
                    self.fetcher._q = _DropOverallProgressQueue(prev_fetcher_q)
                else:
                    prev_fetcher_q = None
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
                    if drop_overall_inline:
                        self.fetcher._q = prev_fetcher_q
                urls = self.fetcher._normalize_loop_result(one) if ok else []
            else:
                urls = self._download_only_urls(pid)

            urls = [u for u in urls if isinstance(u, str) and u.startswith("http")]
            if urls:
                # Seed the pending pages before download so a partial failure /
                # crash leaves a recoverable pending trail in the DB.
                self._seed_pending_urls(pid, urls)
                if emit_phase:
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
                if reserve_timetag_block:
                    # Contiguous timetag block so this PID's pages get
                    # non-interleaved timestamps under concurrency. Thread-local.
                    self.downloader._begin_pid_timetag_block(len(urls))
                    block_begun = True
                if page_progress:
                    _download_call = lambda: self._download_pid_group_with_page_progress(pid, urls)
                else:
                    _download_call = lambda: self.downloader._download_pid_group(pid, urls)
                with diag_log.span(diag_log.WORKER, f"PID {pid} download ({len(urls)} pages)"):
                    account_ok, failed, _ = self._run_with_network_retry(
                        f"PID {pid} 下載", _download_call,
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
                if page_progress:
                    self._clear_page_progress()
        except Exception:
            neutral = True  # non-network failure: not the cookie's fault
            raise
        finally:
            if block_begun:
                with contextlib.suppress(Exception):
                    self.downloader._end_pid_timetag_block()
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
        # leaves ``download_ok`` True and is correctly settled.
        return (failed if isinstance(failed, list) else []), (bool(ok) and download_ok)

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

            workers = self._effective_worker_count(total)
            if workers > 1:
                active = self._scheduler.active_account_count() if self._scheduler else 1
                self._emit(
                    f"<p><font color='green'>並發邊查邊下：同時 {workers} 條"
                    f"（{active} 個有效 Cookie）</font></p>"
                )
                failed_nested = self._run_concurrent(order, total, workers)
            else:
                failed_nested = self._run_sequential(order, total)

            self._finalize(failed_nested)
        except Exception as e:
            from app.core.pixiv_thread_utils import output_err
            self._emit("<p><font color='red'>邊查邊下失敗</font></p>")
            self._emit(output_err(e))
            self._q.put(WorkerEvent("next", -1))

    def _effective_worker_count(self, pending):
        """How many concurrent workers to actually use (>=1)."""
        setting = getattr(self, "combined_workers", 1)
        try:
            if int(setting or 1) <= 1:
                return 1
        except Exception:
            return 1
        active = self._scheduler.active_account_count() if self._scheduler is not None else 1
        return resolve_worker_count(setting, active, pending)

    def _run_sequential(self, order, total):
        """One-PID-at-a-time loop (``combined_workers <= 1``); unchanged."""
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
            # Persist the advanced timetag counter after every PID — combined
            # never runs Step 4's finalize, so without this the next run reuses
            # the same download_time start and stamps duplicate filename prefixes.
            with contextlib.suppress(Exception):
                self.downloader._emit_timechanged()
        return failed_nested

    def _run_concurrent(self, order, total, workers):
        """K-way concurrent variant of :meth:`_run_sequential`.

        Each worker queries+downloads one PID on its own account (the
        scheduler's ``held`` flag guarantees distinct cookies). Workers emit NO
        progress/phase events (the engines' queues are swapped to a drop filter
        for the whole phase, and the core runs with the suppress flags); this
        run() thread is the sole coordinator — it owns overall progress, the
        pending-tracker retire, timetag persistence, and the lightweight
        aggregate phase line. That single-producer discipline is what prevents
        the event flood that froze the GUI in the reverted Phase 1.
        """
        failed_nested = []
        self._active_pids = {}
        # Apply mid-run settings ONCE up front; workers skip the per-PID re-apply
        # to avoid concurrent mutation of the shared engines.
        with contextlib.suppress(Exception):
            self.fetcher._apply_live_settings_if_changed()
            self.downloader._apply_live_settings_if_changed()
        # Swap both engines' queues to drop progress/page_progress for the whole
        # concurrent phase (restored in finally). Other events pass through.
        prev_fq, prev_dq = self.fetcher._q, self.downloader._q
        self.fetcher._q = _DropProgressQueue(prev_fq)
        self.downloader._q = _DropProgressQueue(prev_dq)
        done = 0
        try:
            items = iter(list(enumerate(order)))

            def _next_item():
                for ordinal, (pid, needs) in items:
                    return ordinal, pid, needs
                return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                inflight = set()

                def _submit_one():
                    nxt = _next_item()
                    if nxt is None:
                        return False
                    ordinal, pid, needs = nxt
                    inflight.add(ex.submit(self._process_one_pid_worker, pid, needs, ordinal))
                    return True

                for _ in range(workers):
                    if self._stop_event.is_set() or not _submit_one():
                        break
                while inflight:
                    finished, inflight = concurrent.futures.wait(
                        inflight, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    stop_now = False
                    for fut in finished:
                        try:
                            result = fut.result()
                        except Exception as exc:
                            # A worker raised an unexpected non-network error
                            # (e.g. disk-full on os.replace). Surface it instead
                            # of silently treating it as a clean stop — the
                            # sequential path emits via run()'s except. The PID
                            # stays pending (no _handle_worker_result ran for it).
                            from app.core.pixiv_thread_utils import output_err
                            self._emit(output_err(exc))
                            stop_now = True
                            continue
                        if result is None:
                            # acquire -> None (stop / all disabled): stop refilling.
                            stop_now = True
                            continue
                        self._handle_worker_result(result, failed_nested)
                        done += 1
                        self._q.put(WorkerEvent("progress", (1, total)))
                        with contextlib.suppress(Exception):
                            self.downloader._emit_timechanged()
                        self._emit_aggregate_phase(done, total, workers)
                    if stop_now or self._stop_event.is_set():
                        continue  # drain remaining in-flight, submit no more
                    for _ in range(len(finished)):
                        if not _submit_one():
                            break
        finally:
            self.fetcher._q, self.downloader._q = prev_fq, prev_dq
        return failed_nested

    def _process_one_pid_worker(self, pid, needs_query, ordinal):
        """Concurrent worker body. Returns ``(pid, needs_query, failed, ok)`` or
        ``None`` (acquire -> stop/all-disabled). Emits no progress/phase; the
        coordinator handles those from the returned result."""
        if self._stop_event.is_set():
            return None
        with self._active_lock:
            self._active_pids[str(pid)] = "查詢" if needs_query else "下載"
        try:
            failed, ok = self._process_one_pid_core(
                pid, needs_query,
                emit_phase=False, page_progress=False, drop_overall_inline=False,
                apply_live=False, reserve_timetag_block=True,
            )
            if failed is None:
                return None
            return (pid, needs_query, failed, ok)
        finally:
            with self._active_lock:
                self._active_pids.pop(str(pid), None)

    def _handle_worker_result(self, result, failed_nested):
        """Coordinator-side per-PID bookkeeping (run thread only — serialized)."""
        pid, needs_query, failed, ok = result
        failed_nested.append(failed)
        if needs_query and ok:
            with contextlib.suppress(Exception):
                self.fetcher._mark_pid_processed(pid)

    def _emit_aggregate_phase(self, done, total, workers):
        """Lightweight visible-concurrency line: how many PIDs are in flight and
        which, plus overall completion — replaces the per-PID phase label that
        K workers would otherwise race on."""
        with self._active_lock:
            active = list(self._active_pids.keys())
        shown = "、".join(active) if active else "-"
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent(
                "phase",
                f"邊查邊下中（{len(active)}/{workers} 並發，完成 {done}/{total}）：PID {shown}",
            ))

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
