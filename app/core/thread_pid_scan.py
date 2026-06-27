import contextlib
import os
import datetime
import concurrent.futures
import requests
import random as pyrandom
import threading
from pixiv_api import *
from app.core.metadata_db import MetadataDB, emit_db_stats, mirror_exist_pid_set
from app.core.worker_event import WorkerEvent
from app import i18n
from app.core.pixiv_thread_utils import (
    init_cookie_fields,
    normalize_pid_set,
    safe_json,
    safe_read_json,
)
from app.core.pixiv_thread_base import (
    PauseableThread,
    _cookie_usage_label,
    _format_cookie_usage_summary,
)
# Bookmark-source scan and the incremental-persistence / output-commit groups
# moved to sibling modules (file-size refactor) and mixed back in below so the
# worker's public surface is unchanged.
from app.core.step2_bookmark_scan import _Step2BookmarkMixin
from app.core.step2_incremental_io import _Step2IncrementalIOMixin

global pid_num
pid_num = 0
global pid_len
pid_len = 0
_pid_count_lock = threading.Lock()

class get_pixiv_author_imgID_Thread(PauseableThread, _Step2BookmarkMixin,
                                    _Step2IncrementalIOMixin):
    '''抓取畫師作品下所有圖片的 Pixiv ID'''
    def __init__(self, q, Author_list, Agent, path, cookies, exist_pid, single_thread_mode=False, scheduler=None, stats_collector=None, *, event_log=None, author_order=False, force_rescan=False, source_mode="following", bookmark_scope="all", bookmark_user_id=""):
        super().__init__(q, scheduler=scheduler)
        self.author_order = bool(author_order)
        self.force_rescan = bool(force_rescan)
        self.source_mode = "bookmarks" if str(source_mode) == "bookmarks" else "following"
        self.bookmark_scope = str(bookmark_scope or "all")
        if self.bookmark_scope not in {"public", "private", "all"}:
            self.bookmark_scope = "all"
        self.bookmark_user_id = str(bookmark_user_id or "").strip()
        self.Author_list = Author_list
        self.Agent = Agent
        self.path = path
        self.cookie_entries, self.cookie_pool, self._cookie_alias_map, self.cookies = init_cookie_fields(cookies)
        self.exist_pid = normalize_pid_set(exist_pid)
        self.executor = None
        self.single_thread_mode = single_thread_mode
        self.single_mode_flag = bool(single_thread_mode)
        self._step2_cookie_usage_counts = {}
        self._step2_cookie_usage_seen = set()
        self._last_step2_cookie_label = ""
        self._step2_cookie_usage_lock = threading.Lock()
        self._step2_early_skip_pids = set()
        self._step2_skip_lock = threading.Lock()
        self._stats_collector = stats_collector
        self._event_log = event_log
        self._metadata_db = self._init_metadata_db()
        # No mirror-back: exist_pid is the DB's closed set passed in from
        # ``_build_step2`` (``MetadataDB(path).closed_artwork_set()``), so
        # re-importing it would only re-scan ~1.1M rows and invalidate the
        # closed-set cache for the rest of this run.
        self._emit_metadata_db_stats(stage="Step2")

    def _init_metadata_db(self):
        """Open the SQLite metadata cache (no JSON migration here — Step 2 doesn't read it)."""
        try:
            return MetadataDB(self.path, event_log=getattr(self, "_event_log", None))
        except Exception:
            return None

    def _mirror_exist_pid_to_db(self):
        """Best-effort copy of exist_pid into the SQLite cache."""
        mirror_exist_pid_set(getattr(self, "_metadata_db", None), self.exist_pid)

    def _emit_metadata_db_stats(self, stage="Step2"):
        """Print a one-liner with current SQLite cache size."""
        emit_db_stats(getattr(self, "_metadata_db", None), self._q, stage=stage)

    def flush_for_shutdown(self):
        """Synchronously persist in-progress state and close SQLite for shutdown.

        Originally only closed the DB connection. Now also flushes any PIDs
        the artist scan has accumulated in memory so a crash mid-run doesn't
        lose them. Safe to call from any path (window close, crash hook,
        atexit) — silent-failure throughout so it can't shadow the
        triggering exception.
        """
        try:
            self._flush_step2_incremental(reason="shutdown")
        except Exception:
            pass
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    # 增量儲存：每 N 個作者完成後寫一次，保證崩潰時最多只丟 < 1 分鐘工作。
    # 沒有 in-progress 收集（_collected_pids 還沒初始化）就直接 no-op。
    # _flush_step2_incremental moved to step2_incremental_io._Step2IncrementalIOMixin
    # (file-size refactor); this class attr stays as it's read by
    # _execute_artist_tasks.
    _STEP2_INCREMENTAL_EVERY = 5

    def _record_step2_cookie_usage(self, aid, cookie_value):
        cookie_text = str(cookie_value or "").strip()
        label = _cookie_usage_label(cookie_text, self.cookie_pool, self._cookie_alias_map)
        if not cookie_text:
            with self._step2_cookie_usage_lock:
                self._last_step2_cookie_label = label
            return label
        aid_key = str(aid)
        try:
            with self._step2_cookie_usage_lock:
                self._last_step2_cookie_label = label
                if aid_key not in self._step2_cookie_usage_seen:
                    self._step2_cookie_usage_seen.add(aid_key)
                    self._step2_cookie_usage_counts[label] = int(self._step2_cookie_usage_counts.get(label, 0)) + 1
        except Exception:
            pass
        return label

    def _emit_step2_cookie_usage_summary(self):
        try:
            summary = _format_cookie_usage_summary(self._step2_cookie_usage_counts, self.cookie_pool, self._cookie_alias_map)
            self._q.put(WorkerEvent("output", f"<p><font color='gray'>[PID Cookie統計] {summary}</font></p>"))
        except Exception:
            pass

    def _select_step2_cookie(self):
        try:
            if self.cookie_pool:
                return pyrandom.choice(self.cookie_pool)
        except Exception:
            pass
        return str(self.cookies or "").strip()

    def _run_step2_with_random_cookie(self, aid):
        cookie = self._select_step2_cookie()
        self._record_step2_cookie_usage(aid, cookie)
        return self.thread_no_use_seleium_get_pid(cookie, self.Agent, self.path, '1', aid)

    def _run_step2_with_acquired_cookie(self, aid):
        """Single-thread path: acquire from AccountScheduler, run with
        retry, release.

        Returns the PID list on success, None if scheduler returned None
        (stop signal) or the request failed at the proxy level after all
        retries.
        """
        acc = self._acquire_account()
        if acc is None:
            return None  # stop signal or no accounts
        self._record_step2_cookie_usage(aid, acc.cookie)
        ok = False
        neutral = False
        try:
            ok, result, _ = self._run_with_network_retry(
                f"畫師 {aid}",
                lambda: self.thread_no_use_seleium_get_pid(
                    acc.cookie, self.Agent, self.path, '1', aid, proxies=acc.proxies,
                ),
            )
            return result
        except Exception:
            # Non-network failure (ReadTimeout / SSLError / parse / sqlite):
            # not the cookie's fault, and must not leak the held account.
            neutral = True
            raise
        finally:
            self._release_account_after_work(acc, ok=ok, neutral=neutral)

    # _collect_step2_incremental_pid moved to
    # step2_incremental_io._Step2IncrementalIOMixin (file-size refactor).

    def _init_step2_run_state(self):
        try:
            self._progress_updates = []
            self._progress_updates_lock = threading.Lock()
        except Exception:
            self._progress_updates = []
            self._progress_updates_lock = threading.Lock()
        try:
            self._seen_pids = set()
            pics_file = os.path.join(self.path, 'pictures_id.txt')
            if os.path.isfile(pics_file):
                with open(pics_file, encoding='utf-8') as pf:
                    for line in pf:
                        self._seen_pids.add(line.strip())
        except Exception:
            self._seen_pids = set()
        self._pid_file_lock = threading.Lock()
        try:
            self._collected_pids = []
            self._collected_pids_lock = threading.Lock()
        except Exception:
            self._collected_pids = []
            self._collected_pids_lock = threading.Lock()
        # 增量寫入用：避免兩個 worker 同時 flush；用 try-acquire 跳過已在跑的呼叫
        self._step2_flush_lock = threading.Lock()
        # Serialises the per-artist user_id backfill DB writes across the 2
        # scan workers. MetadataDB already serialises its own writes under an
        # internal lock and opens connections with a 30s busy_timeout, so this
        # is a belt-and-suspenders guard against contention, not a correctness
        # requirement.
        self._step2_db_write_lock = threading.Lock()
        self._step2_artists_done = 0

    def _load_author_progress(self):
        progress_file = os.path.join(self.path, 'author_progress.json')
        progress = safe_read_json(progress_file, {})
        return progress if isinstance(progress, dict) else {}

    def _filter_work_list(self, progress):
        work_list = []
        now = datetime.datetime.now()
        force = getattr(self, "force_rescan", False)
        for aid in self.Author_list:
            last = progress.get(str(aid))
            do_process = True
            if last and not force:
                try:
                    last_dt = datetime.datetime.fromisoformat(last)
                    if (now - last_dt).days < 30:
                        do_process = False
                except Exception:
                    do_process = True
            if do_process:
                work_list.append(aid)
        if force:
            with contextlib.suppress(Exception):
                self._q.put(WorkerEvent("output",
                    "<p><font color='orange'>[強制重掃] 忽略 30 天跳過，重新掃描全部畫家以補齊作者資料</font></p>"))
        try:
            self._q.put(WorkerEvent("output",f"<p><font color='black'>畫師總數：{len(self.Author_list)}，待處理：{len(work_list)}</font></p>"))
        except Exception:
            pass
        if len(work_list) == 0 and len(self.Author_list) > 0:
            try:
                self._q.put(WorkerEvent("output","<p><font color='gray'>[PID增量] 近 30 天內皆已處理，步驟 2 本次不重抓</font></p>"))
            except Exception:
                pass
        return work_list

    def _execute_artist_tasks(self, work_list):
        global pid_len
        with _pid_count_lock:
            pid_len = len(work_list)
        results = []
        if self.single_mode_flag:
            try:
                self._q.put(WorkerEvent("output", "<p><font color='green'>已啟用單執行緒 PID 模式</font></p>"))
                if self._scheduler is not None:
                    avg = self._scheduler.average_cooldown()
                    self._q.put(WorkerEvent("output",
                        f"<p><font color='green'>PID 平均請求頻率：每 {avg:.1f} 秒一次</font></p>"
                    ))
                else:
                    self._q.put(WorkerEvent("output",
                        "<p><font color='gray'>PID scheduler 未注入，使用單一 cookie</font></p>"
                    ))
            except Exception:
                pass
            for aid in work_list:
                if self._stop_event.is_set():
                    break
                try:
                    if self._scheduler is not None:
                        res = self._run_step2_with_acquired_cookie(aid)
                    else:
                        res = self._run_step2_with_random_cookie(aid)
                    if isinstance(res, list):
                        results.append(res)
                except Exception as e:
                    try:
                        self._q.put(WorkerEvent("output", f"<p><font color='red'>畫師 {aid} 取得 PID 失敗：{e}</font></p>"))
                    except Exception:
                        pass
                self._step2_artists_done += 1
                if self._step2_artists_done % self._STEP2_INCREMENTAL_EVERY == 0:
                    self._flush_step2_incremental(reason="loop")
                # No explicit sleep — scheduler.release() set cooldown for next acquire()
        else:
            max_workers = 2
            results = []
            # Route the default multi-thread path through the scheduler when one
            # is wired, so Step 2 honors per-account cooldown, proxy binding (no
            # local-IP leak), and the network retry — exactly like the
            # single-thread/bookmark paths. acquire() is multi-consumer-safe
            # (held flag under lock), so the 2 workers never share an account.
            submit_fn = (
                self._run_step2_with_acquired_cookie
                if self._scheduler is not None
                else self._run_step2_with_random_cookie
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as self.executor:
                futures = [self.executor.submit(submit_fn, aid) for aid in work_list]
                for fut in concurrent.futures.as_completed(futures):
                    if self._stop_event.is_set():
                        break
                    try:
                        res = fut.result()
                    except Exception as e:
                        try:
                            self._q.put(WorkerEvent("output", f"<p><font color='red'>畫師取得 PID 失敗：{e}</font></p>"))
                        except Exception:
                            pass
                        res = None
                    if isinstance(res, list):
                        results.append(res)
                    self._step2_artists_done += 1
                    if self._step2_artists_done % self._STEP2_INCREMENTAL_EVERY == 0:
                        self._flush_step2_incremental(reason="loop")
        return results

    # Bookmark-source path (_bookmark_rest_values / _step2_fetch_bookmark_page /
    # _run_bookmarks_with_cookie / _run_bookmarks_with_acquired_cookie /
    # _append_bookmark_pids / _execute_bookmark_tasks) moved to
    # step2_bookmark_scan._Step2BookmarkMixin (file-size refactor).
    #
    # Incremental persistence + output commit (_persist_author_progress /
    # _collect_step2_pids_from_queue / _merge_step2_pids_with_existing /
    # _append_new_pids_to_file / _persist_pending_pids_to_db /
    # _write_step2_pictures_id / _write_step2_skip_pids /
    # _regroup_pictures_id_by_author / _commit_step2_outputs) moved to
    # step2_incremental_io._Step2IncrementalIOMixin. The worker inherits them
    # all unchanged.

    def run(self):
        global pid_len, pid_num
        with _pid_count_lock:
            pid_num = 0
            pid_len = 0
        self._init_step2_run_state()
        if getattr(self, "source_mode", "following") == "bookmarks":
            results = self._execute_bookmark_tasks()
        else:
            progress = self._load_author_progress()
            work_list = self._filter_work_list(progress)
            results = self._execute_artist_tasks(work_list)
        self._emit_step2_cookie_usage_summary()
        end = [i for item in results for i in item]
        end = [i for i in end if i not in self.exist_pid]
        self._commit_step2_outputs(end)
        if self._stop_event.is_set():
            self._q.put(WorkerEvent("finished", i18n.t("log.pid.stopped")))
            self._q.put(WorkerEvent("next", -1))
        else:
            # Emit finished BEFORE next so the dispatcher's single-drain order is
            # handle_finished (tear down step 2) THEN handle_next (start step 3).
            # The old next-then-finished order made handle_finished re-mark the
            # just-started step 3 as 'done' and disable its pause/stop. (B7)
            self._q.put(WorkerEvent("finished", i18n.t("log.pid.done")))
            self._q.put(WorkerEvent("next", 3))
    def _step2_fetch_artist_pid_list(self, author_pids, cookie, Agent, proxies=None):
        '''發送單一畫師的 profile/all 請求並回傳 PID list。

        回傳 ``None`` 代表「軟失敗」(non-2xx / error envelope / 非 JSON，
        典型為限流或 Cookie 失效)——呼叫端據此**不**記錄作者進度，讓該畫師
        保留待下次重掃，避免把限流誤判成「該畫師 0 作品」而跳過 30 天 (B5)。
        正常回傳 list (dict→keys / list→原值 / 其它→[])。'''
        url = 'https://www.pixiv.net/ajax/user/' + author_pids + '/profile/all?lang=zh_tw'
        headers = {
            'User-Agent': Agent,
            'Cookie': cookie,
            'referer': 'https://www.pixiv.net/users/' + author_pids,
        }
        res = requests.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
        if getattr(res, "status_code", 200) != 200:
            return None
        try:
            payload = res.json()
        except Exception:
            return None
        if isinstance(payload, dict) and payload.get('error'):
            return None
        resdicts = safe_json(res, 'body', 'illusts', default={})
        if isinstance(resdicts, dict):
            return [key for key in resdicts.keys()]
        if isinstance(resdicts, list):
            return list(resdicts)
        return []

    def _step2_emit_incremental_status(self, author_pids, pid_stats):
        '''依 pid_stats 標記輸出截斷或回退提示（吞例外）'''
        if pid_stats.get("used_cutoff"):
            try:
                self._q.put(WorkerEvent("output",
                    "<p><font color='gray'>[PID增量] 畫師 {} 命中既有 PID {}，提前截斷：保留最新 {} 筆，略過 {} 筆舊資料</font></p>".format(
                        author_pids,
                        pid_stats.get("boundary_pid", ""),
                        pid_stats.get("kept_count", 0),
                        pid_stats.get("truncated_count", 0),
                    )
                ))
            except Exception:
                pass
        elif pid_stats.get("fallback_full_scan"):
            try:
                self._q.put(WorkerEvent("output",
                    "<p><font color='gray'>[PID增量] 畫師 {} 偵測到非數字 PID，已回退為全量掃描（{} 筆）</font></p>".format(
                        author_pids,
                        pid_stats.get("input_count", 0),
                    )
                ))
            except Exception:
                pass

    def _step2_backfill_author_user_ids(self, full_pids, author_pids):
        """把整個畫師清單的 user_id 補進 DB（UPDATE-only，不新增列、不影響佇列/截斷）。

        Step 2 掃一個畫師時本來就拿到他全部作品的 PID；即使增量掃描截斷了舊
        PID，這裡仍把『已在 artworks 的』那些舊 PID 補上作者，讓「依作者分組」
        對既有大量待下載資料也能生效，不必逐筆重新查詢。

        只在開啟 author_order（或一次性 force_rescan 重掃）時做（其餘情況零
        成本）。DB 寫入經 ``_step2_db_write_lock`` 序列化，避免兩條掃描 worker
        同時寫；MetadataDB 本身也以內部鎖序列化寫入並設了 30s busy_timeout，
        故此鎖只是額外保險。全程吞例外——best-effort，失敗下次重跑會自癒。
        """
        if not (getattr(self, "author_order", False) or getattr(self, "force_rescan", False)):
            return
        db = getattr(self, "_metadata_db", None)
        if db is None or not full_pids:
            return
        uid = None if author_pids in (None, "") else str(author_pids)
        if not uid:
            return
        lock = getattr(self, "_step2_db_write_lock", None)
        try:
            if lock is not None:
                with lock:
                    db.backfill_user_ids(full_pids, uid)
            else:
                db.backfill_user_ids(full_pids, uid)
        except Exception:
            pass

    def _step2_record_skipped_pids(self, step2_skipped_pid):
        '''把增量略過的 PID 加進 _step2_early_skip_pids（線程安全，吞例外）'''
        try:
            if step2_skipped_pid:
                with self._step2_skip_lock:
                    for spid in step2_skipped_pid:
                        text = str(spid).strip()
                        if text:
                            self._step2_early_skip_pids.add(text)
        except Exception:
            pass

    def _mark_pid_seen(self, npid):
        """Add to _seen_pids under _pid_file_lock; falls back to direct add on lock failure."""
        try:
            with self._pid_file_lock:
                self._seen_pids.add(npid)
        except Exception:
            try:
                self._seen_pids.add(npid)
            except Exception:
                pass

    def _emit_pid_write_error(self, err):
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='red'>寫入 pictures_id 失敗：{err}</font></p>"))
        except Exception:
            pass

    def _step2_append_new_pids(self, pid, author_id=None):
        '''把新 PID 追加到 _collected_pids，並更新 _seen_pids（避免跨 worker 重複，吞例外）。

        ``_collected_pids`` 存的是 ``(pid, user_id)`` tuple——畫師 ID 就是 ``author_id``
        參數，由呼叫端（``thread_no_use_seleium_get_pid``）傳入。``user_id`` 為 ``None``
        時代表無作者資訊（不應發生於正常路徑，僅為防禦寫法）。'''
        try:
            new_pids = [p for p in pid if p not in getattr(self, '_seen_pids', set())]
            if not new_pids:
                return
            uid = None if author_id in (None, "") else str(author_id)
            try:
                with self._collected_pids_lock:
                    for npid in new_pids:
                        if npid in self._seen_pids:
                            continue
                        self._collected_pids.append((npid, uid))
                        self._mark_pid_seen(npid)
            except Exception as e:
                self._emit_pid_write_error(e)
        except Exception:
            pass

    def _step2_record_author_progress(self, author_pids):
        '''記錄作者已完成抓取時間（線程安全，吞例外）'''
        try:
            ts = datetime.datetime.now().isoformat()
            try:
                with self._progress_updates_lock:
                    self._progress_updates.append((str(author_pids), ts))
            except Exception:
                self._progress_updates.append((str(author_pids), ts))
        except Exception:
            try:
                self._q.put(WorkerEvent("output","<p><font color='red'>記錄作者進度失敗</font></p>"))
            except Exception:
                pass

    def _step2_record_artist_failure(self, author_pids, path, num, err):
        '''發生例外時把作者 ID 寫入 authorPids_err{num}.txt'''
        print(err)
        f = open((path + "authorPids_err" + str(num) + ".txt"), "a+")
        f.write(author_pids + '\n')
        f.close()

    def thread_no_use_seleium_get_pid(self, cookie, Agent, path, num, author_pids, proxies=None):
        global pid_num
        global pid_len
        with _pid_count_lock:
            pid_num = pid_num + 1
            _current_pid_num = pid_num
            _current_pid_len = pid_len
        if not self._stop_event.is_set():
            self._q.put(WorkerEvent("progress", (1, _current_pid_len)))
        self._pause_event.wait()
        if self._stop_event.is_set():
            return 'stop'
        if (_current_pid_num % 10 == 0):
            self._q.put(WorkerEvent("output", f"<p><font color='black'>{i18n.t('log.pid.progress', n=_current_pid_num)}</font></p>"))
        try:
            pid = self._step2_fetch_artist_pid_list(author_pids, cookie, Agent, proxies=proxies)
            if pid is None:
                # Soft failure (rate-limit / Cookie expired / non-2xx / error
                # envelope): NOT a genuine "0 works". Do NOT record author
                # progress (which would skip this artist for 30 days) — leave it
                # eligible for the next run. (B5)
                with contextlib.suppress(Exception):
                    self._q.put(WorkerEvent("output",
                        f"<p><font color='orange'>畫師 {author_pids} 取得失敗（疑似限流／Cookie 失效），保留待下次重掃</font></p>"))
                return []
            if self._stats_collector is not None:
                _req_label = _cookie_usage_label(str(cookie or "").strip(), self.cookie_pool, self._cookie_alias_map)
                self._stats_collector.report_request(_req_label)
            pid, step2_skipped_pid, pid_stats = self._collect_step2_incremental_pid(pid)
            # Backfill user_id for the artist's FULL list (kept + truncated),
            # so author-grouping works for already-known older PIDs without
            # re-querying them. No-op when author_order is off.
            self._step2_backfill_author_user_ids(pid + step2_skipped_pid, author_pids)
            self._step2_emit_incremental_status(author_pids, pid_stats)
            self._step2_record_skipped_pids(step2_skipped_pid)
            self._step2_append_new_pids(pid, author_id=author_pids)
            self._step2_record_author_progress(author_pids)
            return pid
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError):
            # Network/proxy failures must propagate so the scheduler-aware
            # caller (_run_step2_with_acquired_cookie) can disable the cookie
            # for this run. Without this re-raise the broad Exception handler
            # below would swallow it and ok=True would be released as success.
            raise
        except Exception as err:
            self._step2_record_artist_failure(author_pids, path, num, err)



