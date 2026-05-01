import time
import json
import os
import datetime
import concurrent.futures
import requests
import random as pyrandom
import threading
from pixiv_api import *
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

class get_pixiv_author_imgID_Thread(PauseableThread):
    '''抓取畫師作品下所有圖片的 Pixiv ID'''
    def __init__(self, q, Author_list, Agent, path, cookies, exist_pid, single_thread_mode=False, scheduler=None):
        super().__init__(q, scheduler=scheduler)
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
        self._step2_early_skip_pids = set()
        self._step2_skip_lock = threading.Lock()
    def __del__(self):
        try:
            executor = getattr(self, 'executor', None)
            if executor is not None:
                executor.shutdown(wait=False)
        except Exception:
            pass
        try:
            self.wait()
        except Exception:
            pass
    
    def _record_step2_cookie_usage(self, aid, cookie_value):
        cookie_text = str(cookie_value or "").strip()
        label = _cookie_usage_label(cookie_text, self.cookie_pool, self._cookie_alias_map)
        self._last_step2_cookie_label = label
        if not cookie_text:
            return label
        aid_key = str(aid)
        try:
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
        """Single-thread path: acquire from AccountScheduler, run, release.

        Returns the PID list on success, None if scheduler returned None
        (stop signal) or the request failed at the proxy level.
        """
        acc = self._acquire_account()
        if acc is None:
            return None  # stop signal or no accounts
        self._record_step2_cookie_usage(aid, acc.cookie)
        proxies = acc.proxies
        ok = True
        result = None
        try:
            result = self.thread_no_use_seleium_get_pid(
                acc.cookie, self.Agent, self.path, '1', aid, proxies=proxies,
            )
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError):
            ok = False
        finally:
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

    def _load_author_progress(self):
        progress_file = os.path.join(self.path, 'author_progress.json')
        progress = safe_read_json(progress_file, {})
        return progress if isinstance(progress, dict) else {}

    def _filter_work_list(self, progress):
        work_list = []
        now = datetime.datetime.now()
        for aid in self.Author_list:
            last = progress.get(str(aid))
            do_process = True
            if last:
                try:
                    last_dt = datetime.datetime.fromisoformat(last)
                    if (now - last_dt).days < 30:
                        do_process = False
                except Exception:
                    do_process = True
            if do_process:
                work_list.append(aid)
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
        pid_len = len(work_list)
        results = []
        if self.single_mode_flag:
            try:
                self._q.put(WorkerEvent("output", "<p><font color='green'>已啟用單執行緒 PID 模式</font></p>"))
                if self._scheduler is not None:
                    avg = self._scheduler.average_cooldown()
                    self._q.put(WorkerEvent("output",
                        f"<p><font color='green'>PID 單帳號平均冷卻：{avg:.0f} 秒</font></p>"
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
                # No explicit sleep — scheduler.release() set cooldown for next acquire()
        else:
            max_workers = 2
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as self.executor:
                results = list(self.executor.map(self._run_step2_with_random_cookie, work_list))
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
        """收集 collected pids（從鎖保護的 buffer）並與 end 合併。回傳 (combined_iterable,)。"""
        try:
            with self._collected_pids_lock:
                collected = list(self._collected_pids)
        except Exception:
            collected = list(getattr(self, '_collected_pids', []))
        return list(end) + collected

    def _merge_step2_pids_with_existing(self, pics_file, combined_pids):
        """讀現有 pictures_id.txt 並 dedup，回傳 (existing_list, new_candidates)。"""
        existing_list = []
        if os.path.isfile(pics_file):
            try:
                with open(pics_file, encoding='utf-8') as pf:
                    existing_list = [line.strip() for line in pf if line.strip()]
            except Exception:
                existing_list = []
        existing_seen = set(existing_list)
        new_candidates = []
        for pid in combined_pids:
            spid = str(pid).strip()
            if not spid or spid in self.exist_pid or spid in existing_seen:
                continue
            new_candidates.append(spid)
            existing_seen.add(spid)
        return existing_list, new_candidates

    def _write_step2_pictures_id(self, end):
        """concern 2：合併 collected pids 並寫入 pictures_id.txt。"""
        pics_file = os.path.join(self.path, 'pictures_id.txt')
        try:
            os.makedirs(self.path, exist_ok=True)
        except Exception:
            pass
        try:
            with open(pics_file, 'a+', encoding='utf-8'):
                pass
        except Exception:
            pass
        combined = self._collect_step2_pids_from_queue(end)
        existing_list, new_candidates = self._merge_step2_pids_with_existing(pics_file, combined)
        if new_candidates:
            try:
                with open(pics_file, 'a+', encoding='utf-8') as pf:
                    for text in new_candidates:
                        pf.write(str(text) + '\n')
            except Exception as e2:
                try:
                    self._q.put(WorkerEvent("output",f"<p><font color='red'>寫入 pictures_id 失敗：{e2}</font></p>"))
                except Exception:
                    pass
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>pictures_id 既有 {len(existing_list)} 筆，新增 {len(new_candidates)} 筆，合計 {len(existing_list) + len(new_candidates)} 筆</font></p>"
            ))
        except Exception:
            pass

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

    def _commit_step2_outputs(self, end):
        progress_file = os.path.join(self.path, 'author_progress.json')
        self._persist_author_progress(progress_file)
        # 原本 pictures_id 與 step2_skip 共用一個 outer try/except: pass，保留同等 silent-failure 邊界
        try:
            self._write_step2_pictures_id(end)
            self._write_step2_skip_pids()
        except Exception:
            pass

    def run(self):
        global pid_len, pid_num
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

    def _step2_append_new_pids(self, pid):
        '''把新 PID 追加到 _collected_pids，並更新 _seen_pids（避免跨 worker 重複，吞例外）'''
        try:
            new_pids = [p for p in pid if p not in getattr(self, '_seen_pids', set())]
            if not new_pids:
                return
            try:
                with self._collected_pids_lock:
                    for npid in new_pids:
                        if npid in self._seen_pids:
                            continue
                        self._collected_pids.append(npid)
                        try:
                            with self._pid_file_lock:
                                self._seen_pids.add(npid)
                        except Exception:
                            try:
                                self._seen_pids.add(npid)
                            except Exception:
                                pass
            except Exception as e:
                try:
                    self._q.put(WorkerEvent("output",f"<p><font color='red'>寫入 pictures_id 失敗：{e}</font></p>"))
                except Exception:
                    pass
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
        pid_num = pid_num + 1
        if not self._stop_event.is_set():
            self._q.put(WorkerEvent("progress", (1, pid_len - 1)))
        self._pause_event.wait()
        if self._stop_event.is_set():
            return 'stop'
        if (pid_num % 10 == 0):
            self._q.put(WorkerEvent("output", f"<p><font color='black'>PID progress: {pid_num}</font></p>"))
        try:
            pid = self._step2_fetch_artist_pid_list(author_pids, cookie, Agent, proxies=proxies)
            pid, step2_skipped_pid, pid_stats = self._collect_step2_incremental_pid(pid)
            self._step2_emit_incremental_status(author_pids, pid_stats)
            self._step2_record_skipped_pids(step2_skipped_pid)
            self._step2_append_new_pids(pid)
            self._step2_record_author_progress(author_pids)
            return pid
        except Exception as err:
            self._step2_record_artist_failure(author_pids, path, num, err)



