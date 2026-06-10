import contextlib
import time
import json
import os
import datetime
import concurrent.futures
import requests
import random as pyrandom
import threading
from pixiv_api import *
from app.core.metadata_db import MetadataDB, emit_db_stats, mirror_exist_pid_set
from app.core.worker_event import WorkerEvent
from app.core.pixiv_thread_utils import (
    atomic_write_text,
    init_cookie_fields,
    normalize_pid,
    normalize_pid_set,
    safe_json,
    safe_read_json,
)
from app.core.pixiv_thread_base import (
    PauseableThread,
    _cookie_usage_label,
    _format_cookie_usage_summary,
)

global pid_num
pid_num = 0
global pid_len
pid_len = 0
_pid_count_lock = threading.Lock()

class get_pixiv_author_imgID_Thread(PauseableThread):
    '''抓取畫師作品下所有圖片的 Pixiv ID'''
    def __init__(self, q, Author_list, Agent, path, cookies, exist_pid, single_thread_mode=False, scheduler=None, stats_collector=None, *, event_log=None, author_order=False, force_rescan=False):
        super().__init__(q, scheduler=scheduler)
        self.author_order = bool(author_order)
        self.force_rescan = bool(force_rescan)
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
    _STEP2_INCREMENTAL_EVERY = 5

    def _flush_step2_incremental(self, reason: str = "incremental") -> None:
        """Best-effort incremental save of pictures_id + author_progress.

        Safe to call from any thread (executor worker, main thread, crash
        hook). All file writes go through atomic helpers; pictures_id is
        merge-appended so concurrent callers can't truncate each other.
        """
        if not hasattr(self, "_collected_pids"):
            return
        lock = getattr(self, "_step2_flush_lock", None)
        if lock is None:
            return
        if not lock.acquire(blocking=False):
            return
        try:
            try:
                progress_file = os.path.join(self.path, "author_progress.json")
                self._persist_author_progress(progress_file)
            except Exception:
                pass
            try:
                self._write_step2_pictures_id([])
            except Exception:
                pass
        finally:
            lock.release()

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
        proxies = acc.proxies
        ok, result, _ = self._run_with_network_retry(
            f"畫師 {aid}",
            lambda: self.thread_no_use_seleium_get_pid(
                acc.cookie, self.Agent, self.path, '1', aid, proxies=proxies,
            ),
        )
        self._release_account(acc, ok=ok)
        return result

    def _collect_step2_incremental_pid(self, raw_pid_list):
        """
        Keep only latest PIDs before the first known exist_pid boundary.
        Sort by PID numeric size (newer PID is larger). If PID is not numeric,
        fallback to non-truncated behavior to avoid missing data.
        """
        ordered = []
        seen = set()
        for raw_pid in raw_pid_list:
            pid = normalize_pid(raw_pid)
            if not pid:
                continue
            if pid in seen:
                continue
            seen.add(pid)
            ordered.append(pid)

        if not ordered:
            return [], [], {
                "input_count": 0,
                "kept_count": 0,
                "truncated_count": 0,
                "boundary_pid": "",
                "used_cutoff": False,
                "sorted_by_pid_size": False,
                "fallback_full_scan": False,
                "fallback_reason": "",
            }

        non_numeric = [pid for pid in ordered if not str(pid).isdigit()]
        if non_numeric:
            return ordered, [], {
                "input_count": len(ordered),
                "kept_count": len(ordered),
                "truncated_count": 0,
                "boundary_pid": "",
                "used_cutoff": False,
                "sorted_by_pid_size": False,
                "fallback_full_scan": True,
                "fallback_reason": "non_numeric_pid",
            }

        sorted_desc = sorted(ordered, key=lambda value: int(value), reverse=True)

        keep = []
        skipped = []
        boundary_pid = ""
        for index, pid in enumerate(sorted_desc):
            if pid in self.exist_pid:
                boundary_pid = pid
                skipped = sorted_desc[index:]
                break
            keep.append(pid)

        truncated_count = len(skipped)
        used_cutoff = bool(boundary_pid)
        return keep, skipped, {
            "input_count": len(sorted_desc),
            "kept_count": len(keep),
            "truncated_count": truncated_count,
            "boundary_pid": boundary_pid,
            "used_cutoff": used_cutoff,
            "sorted_by_pid_size": True,
            "fallback_full_scan": False,
            "fallback_reason": "",
        }

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
        # scan workers (no busy_timeout is set on the connection, so concurrent
        # writers would otherwise risk "database is locked").
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
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as self.executor:
                futures = [self.executor.submit(self._run_step2_with_random_cookie, aid) for aid in work_list]
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

    def _persist_author_progress(self, progress_file):
        """寫入 author_progress.json（concern 1）：silent-failure 一致保留。"""
        try:
            if not (hasattr(self, '_progress_updates') and self._progress_updates):
                return
            try:
                prog = {}
                if os.path.isfile(progress_file):
                    try:
                        with open(progress_file, encoding='utf-8') as pf:
                            prog = json.load(pf)
                    except Exception:
                        prog = {}
                with self._progress_updates_lock:
                    for aid, ts in self._progress_updates:
                        prog[str(aid)] = ts
                tmpfile = progress_file + '.tmp'
                with open(tmpfile, 'w', encoding='utf-8') as pf:
                    json.dump(prog, pf, ensure_ascii=False, indent=2)
                os.replace(tmpfile, progress_file)
            except Exception as e:
                try:
                    self._q.put(WorkerEvent("output",f"<p><font color='red'>寫入 author_progress 失敗：{e}</font></p>"))
                except Exception:
                    pass
        except Exception:
            pass

    def _collect_step2_pids_from_queue(self, end):
        """收集 ``_collected_pids``（鎖保護的 ``(pid, user_id)`` buffer）並與 ``end`` 合併。

        ``end`` 是 run() 階段組出來的 flat PID 字串清單（已過濾 exist_pid），這條路徑
        沒有 user_id 資訊；``_collected_pids`` 來自 worker 在抓 profile/all 時即時
        附上的 author。

        合併策略：先以 ``end`` 的順序為主（保留 ``pictures_id.txt`` 的歷史寫入順序
        契約），用 ``collected`` 建 ``pid -> user_id`` lookup 把 user_id 補上去；
        ``collected`` 中**多出來**（end 沒有，例如 incremental flush 時還沒進 end
        的）的 PID 接在最後。下游 ``_merge_step2_pids_with_existing`` 依 PID 字串
        做 first-occurrence dedup，因此「end 提供順序、collected 提供 user_id」
        兩個語意都被保住。"""
        try:
            with self._collected_pids_lock:
                collected = list(self._collected_pids)
        except Exception:
            collected = list(getattr(self, '_collected_pids', []))
        # Build pid → user_id lookup, keeping the first non-None uid seen.
        uid_lookup = {}
        for entry in collected:
            if not isinstance(entry, tuple) or not entry:
                continue
            pid = str(entry[0])
            uid = entry[1] if len(entry) > 1 else None
            if uid is not None and pid not in uid_lookup:
                uid_lookup[pid] = uid
        end_tuples = [(str(pid), uid_lookup.get(str(pid))) for pid in end]
        return end_tuples + collected

    def _merge_step2_pids_with_existing(self, pics_file, combined_pids):
        """讀現有 pictures_id.txt 並 dedup，回傳 ``(existing_list, new_candidates)``。

        ``combined_pids`` 是 ``(pid, user_id)`` tuple list；``new_candidates``
        保持同樣 tuple 形狀，給下游分頭使用（txt 只寫 PID、DB 寫 PID + user_id）。"""
        existing_list = []
        if os.path.isfile(pics_file):
            try:
                with open(pics_file, encoding='utf-8') as pf:
                    existing_list = [line.strip() for line in pf if line.strip()]
            except Exception:
                existing_list = []
        existing_seen = set(existing_list)
        new_candidates = []
        for entry in combined_pids:
            if isinstance(entry, tuple):
                raw_pid = entry[0]
                uid = entry[1] if len(entry) > 1 else None
            else:
                raw_pid = entry
                uid = None
            spid = str(raw_pid).strip()
            if not spid or spid in self.exist_pid or spid in existing_seen:
                continue
            new_candidates.append((spid, uid))
            existing_seen.add(spid)
        return existing_list, new_candidates

    def _append_new_pids_to_file(self, pics_file: str, new_candidates: list) -> None:
        """寫 pictures_id.txt——只寫 PID 字串，user_id 純由 DB 路徑承載。"""
        try:
            with open(pics_file, 'a+', encoding='utf-8') as pf:
                for entry in new_candidates:
                    pid = entry[0] if isinstance(entry, tuple) else entry
                    pf.write(str(pid) + '\n')
        except Exception as e2:
            self._emit_output(f"<p><font color='red'>寫入 pictures_id 失敗：{e2}</font></p>")

    def _persist_pending_pids_to_db(self, new_candidates: list) -> None:
        """寫 DB——tuples 直接 forward 給 ``upsert_pending_pids``。"""
        db = getattr(self, "_metadata_db", None)
        if db is None or not new_candidates:
            return
        with contextlib.suppress(Exception):
            db.upsert_pending_pids(new_candidates)

    def _write_step2_pictures_id(self, end):
        """concern 2：合併 collected pids 並寫入 pictures_id.txt。"""
        pics_file = os.path.join(self.path, 'pictures_id.txt')
        with contextlib.suppress(Exception):
            os.makedirs(self.path, exist_ok=True)
        with contextlib.suppress(Exception):
            with open(pics_file, 'a+', encoding='utf-8'):
                pass
        combined = self._collect_step2_pids_from_queue(end)
        existing_list, new_candidates = self._merge_step2_pids_with_existing(pics_file, combined)
        if new_candidates:
            self._append_new_pids_to_file(pics_file, new_candidates)
        self._persist_pending_pids_to_db(new_candidates)
        self._emit_output(
            f"<p><font color='gray'>pictures_id 既有 {len(existing_list)} 筆，"
            f"新增 {len(new_candidates)} 筆，合計 {len(existing_list) + len(new_candidates)} 筆</font></p>"
        )

    def _write_step2_skip_pids(self):
        """concern 3：寫入 step2_skip_pid.txt 提前跳過清單。"""
        skip_file = os.path.join(self.path, "step2_skip_pid.txt")
        with self._step2_skip_lock:
            skip_lines = sorted(
                [str(x) for x in self._step2_early_skip_pids if str(x).strip()],
                key=lambda s: int(s) if str(s).isdigit() else str(s),
            )
        atomic_write_text(skip_file, skip_lines, backup=True)
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[PID增量] 已寫入步驟2提前跳過清單：{skip_file}（{len(skip_lines)} 筆）</font></p>"
            ))
        except Exception:
            pass

    def _regroup_pictures_id_by_author(self):
        """最終把 pictures_id.txt 依作者重排（同作者連續，作者不明排最後）。

        只在 run() 收尾呼叫一次——增量 flush 仍維持 append-only（崩潰安全）。
        此時新 PID 的 user_id 已寫進 DB，重排讀整檔 + DB user_id，重用步驟4
        的純函式 compute_author_order。重排前的版本經 atomic_write_text
        (backup=True) 留進 history/。DB 不可用 / 取 uid_map 失敗 / 檔案空 /
        已是分組順序 → 跳過（不報錯）。
        """
        if not getattr(self, "author_order", False):
            return
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return
        pics_file = os.path.join(self.path, "pictures_id.txt")
        try:
            with open(pics_file, encoding="utf-8") as pf:
                pids = [line.strip() for line in pf if line.strip()]
        except Exception:
            return
        if not pids:
            return
        try:
            uid_map = db.user_id_map_for_pids(pids)
        except Exception:
            return
        from app.core.thread_download import compute_author_order
        flat, _ = compute_author_order(pids, uid_map)
        if flat == pids:
            return  # 已是分組順序，免寫
        unknown = sum(1 for p in pids if not str(uid_map.get(p) or "").strip())
        try:
            atomic_write_text(pics_file, flat, backup=True)
        except Exception as e:
            self._emit_output(f"<p><font color='red'>依作者重排 pictures_id 失敗：{e}</font></p>")
            return
        tail = f"，其中 {unknown} 筆作者不明排最後" if unknown else ""
        self._emit_output(
            f"<p><font color='gray'>[作者排序] 已依作者重排 pictures_id.txt"
            f"（{len(flat)} 筆{tail}）</font></p>"
        )

    def _commit_step2_outputs(self, end):
        progress_file = os.path.join(self.path, 'author_progress.json')
        self._persist_author_progress(progress_file)
        # 原本 pictures_id 與 step2_skip 共用一個 outer try/except: pass，保留同等 silent-failure 邊界
        try:
            self._write_step2_pictures_id(end)
            self._write_step2_skip_pids()
            self._regroup_pictures_id_by_author()
        except Exception:
            pass

    def run(self):
        global pid_len, pid_num
        with _pid_count_lock:
            pid_num = 0
            pid_len = 0
        self._init_step2_run_state()
        progress = self._load_author_progress()
        work_list = self._filter_work_list(progress)
        results = self._execute_artist_tasks(work_list)
        self._emit_step2_cookie_usage_summary()
        end = [i for item in results for i in item]
        end = [i for i in end if i not in self.exist_pid]
        self._commit_step2_outputs(end)
        if self._stop_event.is_set():
            self._q.put(WorkerEvent("finished", 'Task finished'))
            self._q.put(WorkerEvent("next", -1))
        else:
            self._q.put(WorkerEvent("next", 3))
            self._q.put(WorkerEvent("finished", '抓取所有PID完成'))
    def _step2_fetch_artist_pid_list(self, author_pids, cookie, Agent, proxies=None):
        '''發送單一畫師的 profile/all 請求並回傳 PID list (dict→keys / list→原值 / 其它→[])'''
        url = 'https://www.pixiv.net/ajax/user/' + author_pids + '/profile/all?lang=zh%27'
        headers = {
            'User-Agent': Agent,
            'Cookie': cookie,
            'referer': 'https://www.pixiv.net/users/' + author_pids,
        }
        res = requests.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
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
        同時寫（連線沒設 busy_timeout）。全程吞例外——best-effort，失敗下次
        重跑會自癒。
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
            self._q.put(WorkerEvent("progress", (1, _current_pid_len - 1)))
        self._pause_event.wait()
        if self._stop_event.is_set():
            return 'stop'
        if (_current_pid_num % 10 == 0):
            self._q.put(WorkerEvent("output", f"<p><font color='black'>PID progress: {_current_pid_num}</font></p>"))
        try:
            pid = self._step2_fetch_artist_pid_list(author_pids, cookie, Agent, proxies=proxies)
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



