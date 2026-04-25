from PyQt5.QtCore import *
import time
import json
import os
import datetime
import concurrent.futures
import requests
import random as pyrandom
import threading
from pixiv_api import *
from app.core.pixiv_thread_utils import (
    apply_cookie_pool_speedup,
    atomic_write_text,
    cookie_speed_divisor,
    init_cookie_fields,
    normalize_pid,
    normalize_pid_set,
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
    _signal = pyqtSignal(int,int)
    _output=pyqtSignal(str)
    _countdown = pyqtSignal(int)
    _finished = pyqtSignal(str)
    _thenext = pyqtSignal(int)
    def __init__(self,Author_list,Agent,path,cookies,exist_pid, single_thread_mode=False, pid_wait_min=10, pid_wait_max=60):
        super().__init__()
        self.Author_list=Author_list
        self.Agent=Agent
        self.path=path
        self.cookie_entries, self.cookie_pool, self._cookie_alias_map, self.cookies = init_cookie_fields(cookies)
        self.exist_pid = normalize_pid_set(exist_pid)
        self.executor = None
        self.single_thread_mode = single_thread_mode
        # explicit local flag for clarity elsewhere in code
        self.single_mode_flag = bool(single_thread_mode)
        self._step2_cookie_usage_counts = {}
        self._step2_cookie_usage_seen = set()
        self._last_step2_cookie_label = ""
        self._step2_early_skip_pids = set()
        self._step2_skip_lock = threading.Lock()
        try:
            self.pid_wait_min = int(pid_wait_min)
            self.pid_wait_max = int(pid_wait_max)
        except Exception:
            self.pid_wait_min, self.pid_wait_max = 10, 60
        if self.pid_wait_min < 1:
            self.pid_wait_min = 1
        if self.pid_wait_max < self.pid_wait_min:
            self.pid_wait_max = self.pid_wait_min
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
            self._output.emit(f"<p><font color='gray'>[PID Cookie統計] {summary}</font></p>")
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
            self._output.emit(f"<p><font color='black'>畫師總數：{len(self.Author_list)}，待處理：{len(work_list)}</font></p>")
        except Exception:
            pass
        if len(work_list) == 0 and len(self.Author_list) > 0:
            try:
                self._output.emit("<p><font color='gray'>[PID增量] 近 30 天內皆已處理，步驟 2 本次不重抓</font></p>")
            except Exception:
                pass
        return work_list

    def _execute_artist_tasks(self, work_list):
        global pid_len
        pid_len = len(work_list)
        results = []
        if self.single_mode_flag:
            try:
                self._output.emit("<p><font color='green'>已啟用單執行緒 PID 模式</font></p>")
                self._output.emit(f"<p><font color='green'>PID 等待區間：{self.pid_wait_min} ~ {self.pid_wait_max} 秒</font></p>")
                self._output.emit(
                    f"<p><font color='green'>PID 多Cookie加速：{len(self.cookie_pool or [])} 組 cookies，等待加速係數 x{cookie_speed_divisor(self.cookie_pool):.2f}</font></p>"
                )
                self._output.emit("<p><font color='green'>PID cookies 已啟用隨機輪選</font></p>")
            except Exception:
                pass
            for aid in work_list:
                if self._isPause == 2:
                    break
                try:
                    res = self._run_step2_with_random_cookie(aid)
                    if isinstance(res, list):
                        results.append(res)
                except Exception as e:
                    try:
                        self._output.emit(f"<p><font color='red'>畫師 {aid} 取得 PID 失敗：{e}</font></p>")
                    except Exception:
                        pass
                if self._isPause == 2:
                    break
                raw_delay = pyrandom.randint(self.pid_wait_min, self.pid_wait_max)
                delay = apply_cookie_pool_speedup(raw_delay, self.cookie_pool)
                try:
                    cookie_label = self._last_step2_cookie_label or "未提供Cookie"
                    self._output.emit(
                        f"<p><font color='green'>[PID等待] 使用 {cookie_label}，等待 {delay} 秒 (畫師 {aid}, 多Cookie加速x{cookie_speed_divisor(self.cookie_pool):.2f}, 原始{raw_delay}秒)</font></p>"
                    )
                except Exception:
                    pass
                self._sleep_with_countdown(delay)
                try:
                    cookie_label = self._last_step2_cookie_label or "未提供Cookie"
                    self._output.emit(f"<p><font color='green'>[PID等待] 等待結束 (畫師 {aid}，cookie={cookie_label})</font></p>")
                except Exception:
                    pass
        else:
            max_workers = 2
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as self.executor:
                results = list(self.executor.map(self._run_step2_with_random_cookie, work_list))
        return results

    def _commit_step2_outputs(self, end):
        progress_file = os.path.join(self.path, 'author_progress.json')
        try:
            if hasattr(self, '_progress_updates') and self._progress_updates:
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
                        self._output.emit(f"<p><font color='red'>寫入 author_progress 失敗：{e}</font></p>")
                    except Exception:
                        pass
        except Exception:
            pass
        try:
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
            try:
                with self._collected_pids_lock:
                    collected = list(self._collected_pids)
            except Exception:
                collected = list(getattr(self, '_collected_pids', []))
            existing_list = []
            if os.path.isfile(pics_file):
                try:
                    with open(pics_file, encoding='utf-8') as pf:
                        existing_list = [line.strip() for line in pf if line.strip()]
                except Exception:
                    existing_list = []
            existing_seen = set(existing_list)
            new_candidates = []
            for pid in (end + collected):
                spid = str(pid).strip()
                if not spid or spid in self.exist_pid or spid in existing_seen:
                    continue
                new_candidates.append(spid)
                existing_seen.add(spid)
            if new_candidates:
                try:
                    with open(pics_file, 'a+', encoding='utf-8') as pf:
                        for text in new_candidates:
                            pf.write(str(text) + '\n')
                except Exception as e2:
                    try:
                        self._output.emit(f"<p><font color='red'>寫入 pictures_id 失敗：{e2}</font></p>")
                    except Exception:
                        pass
            try:
                self._output.emit(
                    f"<p><font color='gray'>pictures_id 既有 {len(existing_list)} 筆，新增 {len(new_candidates)} 筆，合計 {len(existing_list) + len(new_candidates)} 筆</font></p>"
                )
            except Exception:
                pass
            skip_file = os.path.join(self.path, "step2_skip_pid.txt")
            with self._step2_skip_lock:
                skip_lines = sorted(
                    [str(x) for x in self._step2_early_skip_pids if str(x).strip()],
                    key=lambda s: int(s) if str(s).isdigit() else str(s),
                )
            atomic_write_text(skip_file, skip_lines, backup=True)
            try:
                self._output.emit(
                    f"<p><font color='gray'>[PID增量] 已寫入步驟2提前跳過清單：{skip_file}（{len(skip_lines)} 筆）</font></p>"
                )
            except Exception:
                pass
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
        if self._isPause == 2:
            self._finished.emit('Task finished')
            self._thenext.emit(-1)
        else:
            self._thenext.emit(3)
            self._finished.emit('抓取所有PID完成')
    def thread_no_use_seleium_get_pid(self,cookie,Agent,path,num,author_pids):
        global pid_num
        global pid_len
        pid_num=pid_num+1
        if self._isPause!=2:
            self._signal.emit(1,pid_len-1)
        while (self._isPause==1):
            time.sleep(1)
        if self._isPause==2:
            return 'stop'
        if(pid_num%10==0):
            self._output.emit(f"<p><font color='black'>PID progress: {pid_num}</font></p>")
        try:
            url='https://www.pixiv.net/ajax/user/'+author_pids+'/profile/all?lang=zh%27'
            headers = {
            'User-Agent': Agent,
            'Cookie':cookie
            ,'referer': 'https://www.pixiv.net/users/'+author_pids,        
            }
            res = requests.get(url, headers=headers)
            resdicts = res.json()['body']['illusts']
            if isinstance(resdicts, dict):
                pid = [key for key in resdicts.keys()]
            elif isinstance(resdicts, list):
                pid = list(resdicts)
            else:
                pid = []

            pid, step2_skipped_pid, pid_stats = self._collect_step2_incremental_pid(pid)
            if pid_stats.get("used_cutoff"):
                try:
                    self._output.emit(
                        "<p><font color='gray'>[PID增量] 畫師 {} 命中既有 PID {}，提前截斷：保留最新 {} 筆，略過 {} 筆舊資料</font></p>".format(
                            author_pids,
                            pid_stats.get("boundary_pid", ""),
                            pid_stats.get("kept_count", 0),
                            pid_stats.get("truncated_count", 0),
                        )
                    )
                except Exception:
                    pass
            elif pid_stats.get("fallback_full_scan"):
                try:
                    self._output.emit(
                        "<p><font color='gray'>[PID增量] 畫師 {} 偵測到非數字 PID，已回退為全量掃描（{} 筆）</font></p>".format(
                            author_pids,
                            pid_stats.get("input_count", 0),
                        )
                    )
                except Exception:
                    pass
            try:
                if step2_skipped_pid:
                    with self._step2_skip_lock:
                        for spid in step2_skipped_pid:
                            text = str(spid).strip()
                            if text:
                                self._step2_early_skip_pids.add(text)
            except Exception:
                pass
            # filter out existing and already-seen pids for incremental append
            try:
                # 只追加新的 PID，避免與既有資料重複
                new_pids = [p for p in pid if p not in getattr(self, '_seen_pids', set())]
                if new_pids:
                    try:
                        with self._collected_pids_lock:
                            for npid in new_pids:
                                # avoid duplicates across workers
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
                            self._output.emit(f"<p><font color='red'>寫入 pictures_id 失敗：{e}</font></p>")
                        except Exception:
                            pass
            except Exception:
                pass
            # 記錄作者已完成抓取時間，供下次增量判斷
            try:
                ts = datetime.datetime.now().isoformat()
                try:
                    with self._progress_updates_lock:
                        self._progress_updates.append((str(author_pids), ts))
                except Exception:
                    # best-effort append
                    self._progress_updates.append((str(author_pids), ts))
            except Exception:
                try:
                    self._output.emit("<p><font color='red'>記錄作者進度失敗</font></p>")
                except Exception:
                    pass
            return pid
        except Exception as err:
            print(err)
            f = open((path+"authorPids_err"+str(num)+".txt"), "a+")
            f.write(author_pids+'\n')
            f.close() 
        

