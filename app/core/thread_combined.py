import concurrent.futures
import contextlib
import os
import random
import threading
import time
from collections import deque

from app.core.worker_event import WorkerEvent
from app import i18n
from app.core.pixiv_thread_base import PauseableThread
from app.core.pixiv_thread_utils import normalize_pid
from app.core import thread_url_fetch, thread_download, diag_log
from app.core.combined_progress_queues import (
    _CombinedPageProgressQueue,
    _DropOverallProgressQueue,
    _DropProgressQueue,
    _LaneProgressQueue,
)
from app.core.combined_work_lists import _CombinedWorkListsMixin
import pixiv_api


# Hard ceiling on 邊查邊下 concurrency regardless of cookie count, so a huge
# pool can't spawn an unbounded thread fleet.
COMBINED_WORKERS_CAP = 16

# 查詢→下載緩衝：同一帳號在查完 meta 後歇這麼久（隨機取值）才開始抓圖，
# 避免 meta 請求與第一張圖片下載零間隔背靠背連發。
QUERY_TO_DOWNLOAD_PAUSE_RANGE = (1.0, 3.0)

# acquire 前匿名探測（本機 IP、無 cookie、不佔帳號）的併發上限。
# 使用者裁定：探測通道不另做速率限制，只設 8 併發上限。
PROBE_MAX_CONCURRENCY = 8


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
        self._send_rate_lock = threading.Lock()
        self._send_timestamps = deque()
        # Per-thread lane context for the parallel per-worker panel. Created here
        # (not just in _run_concurrent) so sequential mode's finally `del
        # self._lane_ctx_local.ctx` finds the attribute and is a clean no-op
        # rather than a suppressed AttributeError on every PID.
        self._lane_ctx_local = threading.local()

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

        # 匿名探測管線：acquire 前先用本機 IP 免 cookie 問一次 meta。
        # 結果放進 fetcher 的 stash 供 _step3_fetch_artwork_info 消費；
        # _probe_requires_cookie 記錄「匿名看不到」的 PID（帳號查詢跳過匿名段）。
        self._probe_semaphore = threading.BoundedSemaphore(PROBE_MAX_CONCURRENCY)
        self.fetcher._probe_info_results = {}
        self.fetcher._probe_requires_cookie = set()

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
            apply_live=True,
        )
        if failed is None:
            return None
        self._last_pid_ok = ok
        return failed

    def _emit_lane(self, slot, **fields):
        """Push a partial lane-row update (parallel mode per-worker panel).

        No-op when ``slot is None`` (sequential mode). The UI merges the provided
        fields into lane ``slot``'s row. Low volume (one per page / per state
        change), keyed by slot, so K lanes can't flood the dispatcher."""
        if slot is None:
            return
        data = {"slot": int(slot)}
        data.update(fields)
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("lane", data))

    def _make_lane_wait_callback(self, slot):
        """Per-worker acquire-wait observer (parallel mode only; None when
        ``slot`` is None). Streams THIS worker's own remaining cooldown seconds
        into its own lane row — each lane counts down independently instead of
        mirroring one shared global countdown. Deduped on the integer value so
        the ≤2/s acquire poll doesn't emit identical lane events."""
        if slot is None:
            return None
        last = [None]

        def _on_wait(remaining):
            r = int(remaining)
            if r != last[0]:
                last[0] = r
                self._emit_lane(slot, state="等待", wait=r)
        return _on_wait

    def _lane_download(self, pid, urls):
        """Download one PID's pages, resetting this thread's lane page counter at
        the START of each attempt. _run_with_network_retry re-invokes this on a
        proxy/connection error, and _download_pid_group re-runs every url; without
        the reset the _LaneProgressQueue would keep accumulating (capped at total)
        and show ~100% for a PID that is actually re-downloading or will end
        pending. Resetting per attempt keeps the lane bar honest."""
        ctx = getattr(self._lane_ctx_local, "ctx", None)
        if ctx is not None:
            ctx["page"] = 0
        return self.downloader._download_pid_group(pid, urls)

    def _probe_pid_anonymously(self, pid_key):
        """acquire 前的匿名探測（本機 IP、無 cookie、不佔帳號）。

        最多 ``PROBE_MAX_CONCURRENCY`` 併發，無其他節流（使用者裁定）。回傳：

        - ``"stashed"`` — 原始 Pixiv_info 結果（含 404）已放入
          ``fetcher._probe_info_results``，之後的查詢直接消費、不再打網路。
        - ``"needs_cookie"`` — 匿名看不到（R18/受限）；帳號查詢時帶 cookie
          並跳過 Pixiv_info 內建的匿名先試段。
        - ``None`` — 探測不適用（快取新鮮/停止）或失敗（暫時性錯誤），
          回退原本的帳號查詢路徑。
        """
        if self._scheduler is None:
            # 沒有帳號調度（headless stub/測試、無 cookie 場景）就沒有
            # 「省帳號」可言——不探測，也保證單元測試不觸網。
            return None
        self._pause_event.wait()
        if self._stop_event.is_set():
            return None
        with contextlib.suppress(Exception):
            cached = self.fetcher._get_meta(pid_key) or None
            if self.fetcher._step3_cache_is_fresh(cached):
                return None  # 查詢會吃快取、不打網路——探測反而多發一個請求
        try:
            with self._probe_semaphore:
                if self._stop_event.is_set():
                    return None
                with diag_log.span(diag_log.WORKER, f"PID {pid_key} probe(anonymous)"):
                    info = pixiv_api.Pixiv_info(
                        "https://www.pixiv.net/artworks/" + pid_key, Agent=self.Agent,
                    )
        except Exception:
            return None  # 探測失敗不影響主流程（含本機網路錯誤），回退帳號查詢
        if not isinstance(info, list) or info == ["error"]:
            return None
        if info == [404]:
            self.fetcher._probe_info_results[pid_key] = info
            return "stashed"
        try:
            img_url = info[3]
        except Exception:
            return None
        valid = bool(img_url) and str(img_url) != "None"
        with contextlib.suppress(Exception):
            # 讓 _refresh_cookie_requirement 直接讀到最新判定（顯示/統計用）。
            self.fetcher._cookie_requirement_map[pid_key] = (not valid)
        if valid:
            self.fetcher._probe_info_results[pid_key] = info
            return "stashed"
        self.fetcher._probe_requires_cookie.add(pid_key)
        return "needs_cookie"

    def _run_query_accountless(self, pid, drop_overall_inline):
        """探測命中後的無帳號查詢：get_download_url 會消費 stash、不打網路。

        ``cookie_override=""`` 讓 fetcher 不挑池內 cookie（空字串 → 不計
        cookie 使用統計；stash 未命中時退化成一次匿名網路查詢，語義一致）。
        回傳 normalize 後的 url list，或 ``None`` 表示應回退帳號查詢路徑
        （異常/停止——停止時絕不能把 PID 當成功 settle）。
        """
        if self._stop_event.is_set():
            return None
        if drop_overall_inline:
            prev_fetcher_q = self.fetcher._q
            self.fetcher._q = _DropOverallProgressQueue(prev_fetcher_q)
        try:
            with diag_log.span(diag_log.WORKER, f"PID {pid} query(prefetched)"):
                one = self.fetcher.get_download_url(
                    self.path, self.Agent, 1, pid, cookie_override="",
                )
        except Exception:
            return None
        finally:
            if drop_overall_inline:
                self.fetcher._q = prev_fetcher_q
        if self._stop_event.is_set():
            # get_download_url 的早停回傳是 []，與「查無結果」無法區分；
            # 一律回退（下一輪 acquire 會回 None 讓呼叫端中止），PID 保持
            # pending。寧可下次重跑，也不要停止時誤 settle。
            return None
        return self.fetcher._normalize_loop_result(one)

    def _try_prefetch_query(self, pid, *, page_progress, drop_overall_inline, slot):
        """acquire 前的探測＋無帳號查詢。回傳 ``(status, urls)``：

        - ``("settled", None)`` — PID 已在無帳號下 settle（被過濾/404/全部
          已在磁碟），呼叫端直接回 ``([], True)``，完全不佔帳號。
        - ``("urls", kept)`` — 查詢已完成（無帳號），帶著待下載頁進帳號段
          （該次 pickup 以 ``work_units=0`` 計冷卻）。
        - ``("fallback", None)`` — 探測不可用/需要 cookie/失敗/停止，走
          原本的帳號查詢路徑。
        """
        pid_key = normalize_pid(pid) or str(pid)
        if slot is not None:
            self._emit_lane(slot, pid=str(pid), alias="", state="查詢",
                            page=0, total=0, wait=0)
        if self._probe_pid_anonymously(pid_key) != "stashed":
            return "fallback", None
        one = self._run_query_accountless(pid, drop_overall_inline)
        if one is None:
            return "fallback", None
        urls = [u for u in one if isinstance(u, str) and u.startswith("http")]
        kept = self._drop_already_downloaded(pid, urls) if urls else []
        if urls and not kept:
            # 每一頁都已在磁碟：補上帳號路徑 kept-empty 分支相同的收尾記帳。
            self.downloader._maybe_flush_exist_pid(pid)
            self._persist_pid_meta(pid)
        if kept:
            return "urls", kept
        diag_log.log(diag_log.WORKER, f"PID {pid} 匿名探測後無需帳號，已 settle")
        if page_progress:
            self._clear_page_progress()
        if slot is not None:
            self._emit_lane(slot, pid="", alias="", state="等待",
                            page=0, total=0, wait=0)
        return "settled", None

    def _process_one_pid_core(self, pid, needs_query, *, emit_phase,
                              page_progress, drop_overall_inline, apply_live,
                              slot=None):
        """Core query+download for one PID. Returns ``(failed, ok)``.

        ``failed is None`` signals acquire returned no account (stop / all
        disabled). The flags let the concurrent path suppress per-PID phase /
        page-progress events (the coordinator owns those), skip the per-call
        queue swaps (the engines' queues are swapped once for the whole
        concurrent phase), and skip the per-PID live-settings re-apply (done
        once up front). The per-PID shared timetag is owned by
        ``download_thread._download_pid_group`` (the common download choke
        point), so concurrent PIDs still get disjoint, non-interleaved stamps.
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
                phase_key = "log.phase.querying" if needs_query else "log.phase.downloading"
                self._q.put(WorkerEvent("phase", i18n.t(phase_key, pid=pid)))
        diag_log.log(diag_log.WORKER, f"PID {pid} 開始 (needs_query={needs_query})")
        # acquire 前的匿名探測管線：能免帳號 settle 的 PID（被過濾/404/已在
        # 磁碟）直接結束；查到待下載頁的帶著 urls 進帳號段（work_units=0）；
        # 其餘（需要 cookie/探測失敗）回退原本的帳號查詢。
        prefetched_urls = None
        if needs_query:
            status, kept = self._try_prefetch_query(
                pid, page_progress=page_progress,
                drop_overall_inline=drop_overall_inline, slot=slot,
            )
            if status == "settled":
                return [], True
            if status == "urls":
                prefetched_urls = kept
        # Lane shows "等待 cookie" during the acquire wait — that wait IS the
        # per-account cooldown, so the user sees each lane pause between PIDs.
        # wait=0 clears any stale countdown left by the previous PID's wait.
        self._emit_lane(slot, pid=str(pid), alias="", state="等待", page=0,
                        total=0, wait=0)
        # The acquire span's elapsed time IS the visible cooldown wait. When it
        # logs ~0.00s the account was ready immediately (cooldown absorbed by the
        # previous PID's download) — that is exactly why no 倒數 shows.
        on_wait = self._make_lane_wait_callback(slot)
        with diag_log.span(diag_log.WORKER, f"PID {pid} acquire(cooldown)"):
            acc = (self._acquire_account(on_wait=on_wait) if on_wait is not None
                   else self._acquire_account())
        if acc is None:
            diag_log.log(diag_log.WORKER, f"PID {pid} acquire -> None (stop/all-disabled)")
            return None, False  # stop / all disabled -> caller breaks
        self._record_pid_send()
        diag_log.log(diag_log.WORKER, f"PID {pid} 取得帳號 alias={getattr(acc, 'alias', '?')}")
        # Lane now shows which cookie this worker holds + whether it is querying.
        # 探測已代付查詢時，這次 pickup 直接進下載，不再顯示 查詢。
        if slot is not None:
            _will_query = needs_query and prefetched_urls is None
            self._emit_lane(slot, pid=str(pid), alias=getattr(acc, "alias", ""),
                            state="查詢" if _will_query else "下載", page=0, total=0)
        sess = pixiv_api.make_session(acc.proxy_url)
        failed = []
        ok = True
        account_ok = True
        download_ok = True
        neutral = False
        page_count = 0  # pages actually downloaded this pickup (for cooldown scaling)
        try:
            if prefetched_urls is not None:
                # 匿名探測已完成查詢與 resume 過濾：這次 pickup 帳號只負責下載。
                urls = list(prefetched_urls)
            elif needs_query:
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
            if needs_query and urls and prefetched_urls is None:
                # Resume filter: a previous partial/stopped run persisted its
                # completed pages per-page (_flush_partial_done); drop them so
                # the re-queried PID downloads only what is still pending
                # instead of duplicating those pages under a new timetag.
                # (探測路徑已在 _try_prefetch_query 做過同樣的過濾。)
                kept = self._drop_already_downloaded(pid, urls)
                if not kept:
                    # Every page already on disk from an earlier run: finish
                    # the bookkeeping a clean download would have done so the
                    # PID actually closes instead of re-querying forever.
                    self.downloader._maybe_flush_exist_pid(pid)
                    self._persist_pid_meta(pid)
                urls = kept
            page_count = len(urls)
            if urls:
                if needs_query and prefetched_urls is None:
                    # 查詢→下載緩衝：同一帳號查完 meta 後先歇 1-3 秒再開始抓圖
                    # （stop 可中斷；stop 後下載迴圈本身也會立即退出並走
                    # download_ok=False 的既有停止路徑）。探測代付查詢時帳號
                    # 這次 pickup 的第一個請求就是下載本身，冷卻已隔開，不再緩衝。
                    self._wait_interruptible(
                        random.uniform(*QUERY_TO_DOWNLOAD_PAUSE_RANGE))
                # Seed the pending pages before download so a partial failure /
                # crash leaves a recoverable pending trail in the DB.
                self._seed_pending_urls(pid, urls)
                if emit_phase:
                    with contextlib.suppress(Exception):
                        self._q.put(WorkerEvent("phase", i18n.t("log.phase.downloading", pid=pid)))
                self.downloader._set_current_download_account(acc)
                # Bind THIS account's cookie to the PID so the download leg sends
                # the held account's cookie over the held account's proxy (the
                # hard cookie<->IP contract). Without this seed,
                # _select_cookie_for_pid would pick a RANDOM pool cookie and send
                # it over acc's IP — a mismatch Pixiv anti-fraud flags. Mirrors
                # standalone Step 4 (_download_pid_with_scheduler).
                self.downloader._pid_cookie_selection[normalize_pid(pid) or str(pid)] = acc.cookie
                if slot is not None:
                    # Per-worker lane: seed the bar at 0/total, then set this
                    # thread's ctx so the phase-wide _LaneProgressQueue streams
                    # per-file progress into this slot's row. Downloads call the
                    # shared downloader directly — NO per-PID queue swap (that
                    # would race on the shared downloader._q across K workers).
                    _alias = getattr(acc, "alias", "")
                    self._emit_lane(slot, pid=str(pid), alias=_alias,
                                    state="下載", page=0, total=len(urls))
                    self._lane_ctx_local.ctx = {
                        "slot": slot, "pid": str(pid), "alias": _alias,
                        "total": len(urls), "page": 0,
                    }
                    _download_call = lambda: self._lane_download(pid, urls)
                elif page_progress:
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
                if not download_ok:
                    # Partial/stopped: persist the pages that DID land so the
                    # next run resumes from them. _completed_urls only ever
                    # holds pages confirmed on disk (ret 0/-1), so this can
                    # never close an unattempted page — the rest of the PID
                    # stays pending (stop ≠ success still holds).
                    self._flush_partial_done(urls)
                if download_ok:
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
                # A PID that resolved to zero usable urls (revoked / filtered /
                # query exhausted) must not leave its lane stuck on 查詢/下載.
                if slot is not None:
                    self._emit_lane(slot, pid="", alias="", state="等待",
                                    page=0, total=0, wait=0)
        except Exception:
            neutral = True  # non-network failure: not the cookie's fault
            raise
        finally:
            # Drop this thread's lane ctx so a later stray "progress" can't be
            # mis-attributed to the finished PID.
            with contextlib.suppress(AttributeError):
                del self._lane_ctx_local.ctx
            self.downloader._clear_current_download_account()
            # Stop mid-PID or a non-network error releases NEUTRALLY: don't credit
            # the cookie with a success (ok=True would refresh its trust window for
            # work the user aborted) nor disable it. Off those paths, a
            # network-exhausted account (account_ok False) still disables as before.
            # Cooldown = one full avg for the pickup + a flat 5 s per page
            # downloaded (PAGE_COOLDOWN_SEC), so a multi-page PID rests a bit
            # longer without sidelining the cookie for pages × avg. 探測已
            # 代付查詢的 pickup 帳號沒發 ajax 查詢，work_units=0（只計分頁）。
            self._release_account_after_work(
                acc, ok=ok and account_ok, neutral=neutral,
                work_units=0 if prefetched_urls is not None else 1,
                pages=page_count,
            )
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

    def _flush_partial_done(self, urls):
        """Persist per-page progress of a partial/stopped PID.

        Marks as downloaded only the pages the downloader confirmed on disk
        (``_completed_urls``: ret 0 = downloaded, ret -1 = already present).
        Unattempted / failed / mid-stream-stopped pages are never in that set,
        so they stay pending and resume next run."""
        dl = self.downloader
        try:
            with dl._completed_urls_lock:
                done = [u for u in urls if u in dl._completed_urls]
        except Exception:
            return
        if done:
            self._mark_urls_done(done)

    def _drop_already_downloaded(self, pid, urls):
        """Resume filter: drop pages whose ``pages`` row is already
        ``status='downloaded'``. Unparseable urls (e.g. ugoira zips) are kept —
        safe default (whole-PID behavior unchanged for them)."""
        db = self.fetcher._metadata_db
        if db is None or not urls:
            return urls
        try:
            done = db.downloaded_page_indices(pid)
        except Exception:
            return urls
        if not done:
            return urls
        from app.core.pid_filesystem import parse_pid_and_page_from_url
        kept = []
        for u in urls:
            _, pidx = parse_pid_and_page_from_url(str(u))
            if pidx is None or pidx not in done:
                kept.append(u)
        return kept

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
            self._emit(f"<p><font color='red'>{i18n.t('log.combined.start')}</font></p>")
            self.fetcher._reset_run_counters()
            query_pids, download_only = self._build_work_lists()
            # Resolve the (optionally author-grouped) iteration order once so the
            # progress denominator always matches what we actually iterate.
            order = self._resolve_combined_order(query_pids, download_only)
            total = len(order)
            # Pre-allocate one timetag per PID by iteration position (persists the
            # advanced cursor once). Combined never runs Step 4's finalize, so this
            # up-front assign is the only place the cursor is advanced/persisted.
            self.downloader.assign_pid_timetags([p for p, _ in order])
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
        return failed_nested

    def _run_concurrent(self, order, total, workers):
        """K-way concurrent variant of :meth:`_run_sequential`.

        Each worker queries+downloads one PID on its own account (the
        scheduler's ``held`` flag guarantees distinct cookies). The coordinator
        (this run() thread) stays the SOLE producer of OVERALL progress (one tick
        per finished PID), the pending-tracker retire, and the aggregate phase
        line — so the 整體進度 bar can never be raced. Workers emit only their own
        low-rate, slot-keyed ``lane`` events (one per page, throttled in
        :class:`_LaneProgressQueue`) for the per-worker panel; the fetcher's query
        ``progress`` is still dropped (``_DropProgressQueue``). This bounded,
        single-overall-producer discipline is what keeps the GUI responsive
        (the reverted Phase 1 flooded the dispatcher with unbounded per-page
        events from every engine).
        """
        failed_nested = []
        self._active_pids = {}
        # Per-worker lane slots: each in-flight PID claims a distinct index in
        # [0, workers) for its UI row; freed on the worker's exit and reused.
        self._free_slots = list(range(workers))
        self._slot_lock = threading.Lock()
        # self._lane_ctx_local (created in __init__) is the per-thread lane ctx
        # read by the shared _LaneProgressQueue — thread-local so K workers
        # sharing one downloader._q never clobber each other's page counters.
        self._q.put(WorkerEvent("lanes_init", {"count": workers}))
        # Apply mid-run settings ONCE up front; workers skip the per-PID re-apply
        # to avoid concurrent mutation of the shared engines.
        with contextlib.suppress(Exception):
            self.fetcher._apply_live_settings_if_changed()
            self.downloader._apply_live_settings_if_changed()
        # Phase-wide queue swaps (restored in finally). The fetcher's overall
        # query "progress" is dropped. The downloader's queue is set ONCE to a
        # _LaneProgressQueue that turns each per-file "progress" into a slot-keyed
        # "lane" event using the CALLING THREAD's ctx — set/cleared by each worker
        # around its own download. Installing it once (not per-PID) avoids racing
        # on the shared downloader._q. Other events (output / countdown / lane /
        # phase / finished / next) pass through.
        prev_fq, prev_dq = self.fetcher._q, self.downloader._q
        self.fetcher._q = _DropProgressQueue(prev_fq)
        self.downloader._q = _LaneProgressQueue(
            prev_dq, lambda: getattr(self._lane_ctx_local, "ctx", None))
        done = 0
        try:
            items = enumerate(order)

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
                        self._emit_aggregate_phase(done, total, workers)
                    if stop_now or self._stop_event.is_set():
                        continue  # drain remaining in-flight, submit no more
                    for _ in range(len(finished)):
                        if not _submit_one():
                            break
        finally:
            self.fetcher._q, self.downloader._q = prev_fq, prev_dq
            with contextlib.suppress(Exception):
                self._q.put(WorkerEvent("lanes_clear", None))
        return failed_nested

    def _claim_slot(self):
        with self._slot_lock:
            return self._free_slots.pop(0) if self._free_slots else None

    def _release_slot(self, slot):
        if slot is None:
            return
        with self._slot_lock:
            self._free_slots.append(slot)

    def _process_one_pid_worker(self, pid, needs_query, ordinal):
        """Concurrent worker body. Returns ``(pid, needs_query, failed, ok)`` or
        ``None`` (acquire -> stop/all-disabled). Emits per-worker lane events
        (keyed by a claimed slot); the coordinator owns overall progress."""
        if self._stop_event.is_set():
            return None
        slot = self._claim_slot()
        with self._active_lock:
            self._active_pids[str(pid)] = "查詢" if needs_query else "下載"
        try:
            failed, ok = self._process_one_pid_core(
                pid, needs_query,
                emit_phase=False, page_progress=False, drop_overall_inline=False,
                apply_live=False, slot=slot,
            )
            if failed is None:
                return None
            return (pid, needs_query, failed, ok)
        finally:
            with self._active_lock:
                self._active_pids.pop(str(pid), None)
            self._release_slot(slot)

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
        send_rate = self._current_send_rate_per_sec()
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent(
                "phase",
                f"邊查邊下中（{len(active)}/{workers} 並發，發送 {send_rate:.1f}/s，"
                f"完成 {done}/{total}）：PID {shown}",
            ))

    def _record_pid_send(self):
        # Stub-safe: tests build combined_thread via __new__ (no __init__),
        # so the send-rate state may be absent — the metric is display-only.
        if getattr(self, "_send_rate_lock", None) is None:
            return
        now = time.monotonic()
        with self._send_rate_lock:
            self._send_timestamps.append(now)
            self._trim_send_timestamps_locked(now)

    def _current_send_rate_per_sec(self) -> float:
        now = time.monotonic()
        with self._send_rate_lock:
            self._trim_send_timestamps_locked(now)
            return float(len(self._send_timestamps))

    def _trim_send_timestamps_locked(self, now: float) -> None:
        cutoff = now - 1.0
        while self._send_timestamps and self._send_timestamps[0] <= cutoff:
            self._send_timestamps.popleft()

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
        # JXL side: combined never runs Step 4's finalize, so wait for the
        # background conversion backlog here — a stop must not end the run
        # (nor release the stop spinner) until every queued file is
        # converted. _drain_jxl_queue keeps the stop spinner text updated
        # with the remaining count.
        with contextlib.suppress(Exception):
            if getattr(self.downloader, "jxl_enable", False):
                if any(t.is_alive() for t in self.downloader._jxl_worker_threads):
                    self._emit("<p><font color='gray'>等待背景 JXL 轉檔完成...</font></p>")
                self.downloader._drain_jxl_queue()
        self._emit(f"<p><font color='green'>{i18n.t('log.combined.done')}</font></p>")
        self._q.put(WorkerEvent("finished", i18n.t("log.combined.done")))
        self._q.put(WorkerEvent("next", -1))  # terminal: Run All stops here

    def flush_for_shutdown(self):
        with contextlib.suppress(Exception):
            self.fetcher._flush_url_meta_snapshot(full=True)
        self._close_metadata_db_quietly(self.fetcher)
