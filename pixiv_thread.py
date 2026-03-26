from PyQt5.QtCore import *
import time
import json
import os
import random
import random as pyrandom
import time
from functools import partial
from queue import Queue
import concurrent.futures
import time
import requests
import numpy as np
from pathlib import Path
from pixiv_api import *
import tag_edit
import pixiv_api
import datetime
import re
import zipfile
import imageio,glob,sys,traceback
import threading
import shutil
import subprocess
global pid_num
pid_num=0
global pid_len
pid_len=0
def output_err(e):
    error_class = e.__class__.__name__ #取得錯誤類型
    detail = e.args[0] #取得詳細內容
    cl, exc, tb = sys.exc_info() #取得Call Stack
    lastCallStack = traceback.extract_tb(tb)[-1] #取得Call Stack的最後一筆資料
    fileName = lastCallStack[0] #取得發生的檔案名稱
    lineNum = lastCallStack[1] #取得發生的行號
    funcName = lastCallStack[2] #取得發生的函數名稱
    errMsg = "File \"{}\", line {}, in {}: [{}] {}".format(fileName, lineNum, funcName, error_class, detail)
    return(errMsg)


def _ensure_history_dir(file_path):
    try:
        d = os.path.dirname(file_path) or os.getcwd()
        hist = os.path.join(d, 'history')
        os.makedirs(hist, exist_ok=True)
        return hist
    except Exception:
        return None


def backup_file(file_path, max_history=10):
    try:
        if not os.path.exists(file_path):
            return
        hist = _ensure_history_dir(file_path)
        if not hist:
            return
        ts = datetime.datetime.now().strftime('%Y%m%d')
        base = os.path.basename(file_path)
        dst = os.path.join(hist, f"{base}.{ts}")
        if os.path.exists(dst):
            idx = 1
            while True:
                candidate = os.path.join(hist, f"{base}.{ts}.{idx}")
                if not os.path.exists(candidate):
                    dst = candidate
                    break
                idx += 1
        shutil.copy2(file_path, dst)
        try:
            files = [f for f in os.listdir(hist) if f.startswith(base + '.')]
            files.sort(key=lambda x: os.path.getmtime(os.path.join(hist, x)), reverse=True)
            while len(files) > max_history:
                old_file = os.path.join(hist, files.pop(0))
                os.remove(old_file)
        except Exception:
            pass
    except Exception:
        pass


def atomic_write_text(file_path, lines, encoding='utf-8', backup=True):
    try:
        if backup:
            try:
                backup_file(file_path)
            except Exception:
                pass
        tmp = file_path + '.tmp'
        dirp = os.path.dirname(file_path)
        if dirp:
            os.makedirs(dirp, exist_ok=True)
        with open(tmp, 'w', encoding=encoding) as f:
            if isinstance(lines, (list, tuple)):
                f.writelines([str(x) + '\n' for x in lines])
            else:
                f.write(str(lines))
        os.replace(tmp, file_path)
    except Exception:
        # best-effort fallback
        try:
            with open(file_path, 'w', encoding=encoding) as f:
                if isinstance(lines, (list, tuple)):
                    f.writelines([str(x) + '\n' for x in lines])
                else:
                    f.write(str(lines))
        except Exception:
            pass


def atomic_write_json(file_path, obj, encoding='utf-8', backup=True):
    try:
        if backup:
            try:
                backup_file(file_path)
            except Exception:
                pass
        tmp = file_path + '.tmp'
        dirp = os.path.dirname(file_path)
        if dirp:
            os.makedirs(dirp, exist_ok=True)
        with open(tmp, 'w', encoding=encoding) as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, file_path)
    except Exception:
        try:
            with open(file_path, 'w', encoding=encoding) as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

class get_following(QThread):
    '''獲得關注帳號畫師'''
    _signal = pyqtSignal(int,int)
    _output=pyqtSignal(str)
    _finished = pyqtSignal(str)
    _thenext = pyqtSignal(int)
    def __init__(self,userid,cookies,Agent,hide_mode):
        super(get_following,self).__init__()
        self.userid=userid
        self.cookies=cookies
        self.Agent=Agent
        self.path=os.getenv('APPDATA')+r'/pixiv_download/'
        self._isPause = 0
        self._partial_following = []
        self._partial_lock = threading.Lock()
        try:
            self.hide = hide_mode.isChecked()
        except Exception:
            self.hide = False
        self.max=0

    def _flush_following_snapshot(self):
        try:
            with self._partial_lock:
                texts = np.unique(self._partial_following).tolist() if self._partial_following else []
            # 同步寫入 txt + json，並保留歷史備份
            try:
                atomic_write_text(os.path.join(self.path, "following.txt"), texts, backup=True)
            except Exception:
                pass
            try:
                atomic_write_json(os.path.join(self.path, "following.json"), texts, backup=True)
            except Exception:
                pass
        except Exception:
            pass

    def pause(self):
        self._output.emit("<p><font color='red'>暫停</font></p>")
        self._isPause = 1
        self._flush_following_snapshot()

    def resume(self):
        self._output.emit("<p><font color='red'>繼續</font></p>")
        self._isPause = 0

    def stop(self):
        self._output.emit("<p><font color='red'>中止</font></p>")
        self._isPause = 2 
        self._flush_following_snapshot()

    def get_follow_illust(self,id,headers,state,times):
        '''獲得所有你關注的畫師 需輸入查詢的ID 第幾個 偽裝 公開/私人'''
        while self._isPause == 1:
            time.sleep(1)
        if self._isPause == 2:
            return []
        global pid_num
        pid_num=pid_num+100
        url = ('https://www.pixiv.net/ajax/user/{}/following?offset='+str(times)+'&limit=100&rest='+state+'&tag=&lang=zh_tw')
        res = requests.get(url.format(id), headers=headers)
        resdicts = res.json()['body']['users'] 
        self._signal.emit(100,self.max)
        i=[]
        try:
            for resdict in resdicts:
                #print(resdict.get('userId'))
                i.append(resdict.get('userId'))
            if i:
                with self._partial_lock:
                    self._partial_following.extend(i)
                self._flush_following_snapshot()
        except Exception as e:
            print(output_err(e))
        #i=[int(_.get('userId')) for _ in resdicts]
        return i
    def illusts(self):              #輸入你的id得到你所有關注的P站畫師
        headers = {
            'User-Agent': self.Agent,
            'Cookie':self.cookies
            ,'referer': 'https://www.pixiv.net/users/'+str(self.userid)+'/following',        
        }
        times=0
        url = ('https://www.pixiv.net/ajax/user/{}/following?offset='+str(times)+'&limit=1&rest=show&tag=&lang=zh_tw') # 訪問存有畫師所有作品
        print(url.format(self.userid))

        res = requests.get(url.format(self.userid), headers=headers)
        print(res.text)
        show_total_num=(res.json()['body']['total'])
        show_list = list(range(0, show_total_num+200, 100))

        if (self.hide==False):
            #print("yes")
            url = ('https://www.pixiv.net/ajax/user/{}/following?offset='+str(times)+'&limit=1&rest=hide&tag=&lang=zh_tw')
            res = requests.get(url.format(self.userid), headers=headers)
            hide_total_num=(res.json()['body']['total'])
            hide_list=[i for i in range(0,hide_total_num+200,100)]
            self.max=int(hide_total_num+show_total_num)
        else:
            self.max=int(show_total_num)
        self._output.emit('一共'+str(self.max)+'個')
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as self.executor:
            func=partial(self.get_follow_illust,self.userid,headers,'show')
            pixiv_following = list(self.executor.map(func,show_list))
            results1=([i for item in pixiv_following for i in item]) 
            #print(len(results1))
        if (self.hide==False):
        
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as self.executor:
                func=partial(self.get_follow_illust,self.userid,headers,'hide')
                pixiv_following2 = list(self.executor.map(func,hide_list))
                results2=([i for item in pixiv_following2 for i in item]) 
                #print(len(results2))
                return results1+results2
        else:
            return results1
    def run(self):
        try:
            all_pixiv_ids = self.illusts()
            texts = np.unique(all_pixiv_ids).tolist()
            self._output.emit('正在寫入數據至following裡面')
            with open((self.path+"/following.txt"), "w+") as f:
                f.write('\n'.join('%s' %aid for aid in texts))
            try:
                with open((self.path+"/following.json"), "w+", encoding='utf-8') as f:
                    json.dump(texts, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            self._output.emit("<p><font color='red'>獲得關注帳號畫師完成</font></p>")
            self._thenext.emit(2)
            self._finished.emit('獲得關注帳號畫師完成')
        except Exception as e:
            self._output.emit('獲得關注失敗 以下為報錯訊息')
            self._output.emit(output_err(e))
            self._thenext.emit(-1)
    def __del__(self):
        self.wait()

class get_pixiv_author_imgID_Thread(QThread):
    '''獲取P站畫家底下所有圖片的pixiv id'''
    _signal = pyqtSignal(int,int)
    _output=pyqtSignal(str)
    _countdown = pyqtSignal(int)
    _finished = pyqtSignal(str)
    _thenext = pyqtSignal(int)
    _isPause=0
    def __init__(self,Author_list,Agent,path,cookies,exist_pid, single_thread_mode=False, pid_wait_min=10, pid_wait_max=60):
        super(get_pixiv_author_imgID_Thread,self).__init__()
        self.Author_list=Author_list
        self.Agent=Agent
        self.path=path
        self.cookies=cookies
        #print(self.cookies)
        self.exist_pid=exist_pid
        self.executor = None
        self.single_thread_mode = single_thread_mode
        # explicit local flag for clarity elsewhere in code
        self.single_mode_flag = bool(single_thread_mode)
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
        self.wait()
    
    def pause(self):
        self._output.emit("<p><font color='red'>暫停</font></p>")
        self._isPause = 1

    def resume(self):
        self._output.emit("<p><font color='red'>繼續</font></p>")
        self._isPause = 0
        
    def stop(self):
        self._output.emit("<p><font color='red'>中止</font></p>")
        self._isPause = 2 

    def _sleep_with_countdown(self, delay):
        """單執行緒超慢速等待（保證實際等待秒數，並支援暫停/中止）"""
        if delay <= 0:
            return
        for remaining in range(int(delay), 0, -1):
            if self._isPause == 2:
                break
            while self._isPause == 1:
                time.sleep(1)
            try:
                self._countdown.emit(remaining)
            except Exception:
                pass
            time.sleep(1)
        try:
            self._countdown.emit(0)
        except Exception:
            pass

    def run(self):
        global pid_len
        global pid_num
        pid_num=0
        pid_len=0
        # load progress file to allow resume; format: {artist_id: last_processed_iso}
        progress_file = os.path.join(self.path, 'author_progress.json')
        progress = {}
        try:
            if os.path.isfile(progress_file):
                with open(progress_file, 'r', encoding='utf-8') as pf:
                    progress = json.load(pf)
        except Exception:
            progress = {}
        # prepare an in-memory collection for worker-reported progress updates
        # workers should append to this list under lock; main thread will commit once
        try:
            self._progress_updates = []
            self._progress_updates_lock = threading.Lock()
        except Exception:
            self._progress_updates = []
            self._progress_updates_lock = threading.Lock()

        # Only process artists that have not been processed within last 30 days
        work_list = []
        now = datetime.datetime.now()
        for aid in self.Author_list:
            last = progress.get(str(aid))
            do_process = True
            if last:
                try:
                    last_dt = datetime.datetime.fromisoformat(last)
                    delta = now - last_dt
                    if delta.days < 30:
                        do_process = False
                except Exception:
                    do_process = True
            if do_process:
                work_list.append(aid)

        try:
            self._output.emit("<p><font color='black'>畫師總數: {}，待處理數: {}</font></p>".format(len(self.Author_list), len(work_list)))
        except Exception:
            pass

        # 若單執行緒模式但被 progress 過濾到 0，改為全量執行，避免看起來像是休眠失效
        if self.single_mode_flag and len(work_list) == 0 and len(self.Author_list) > 0:
            work_list = list(self.Author_list)
            try:
                self._output.emit("<p><font color='orange'>單執行緒模式：偵測到待處理數為 0，已改為全量執行以套用休眠</font></p>")
            except Exception:
                pass

        # prepare seen pids set and file lock for incremental persistence
        try:
            self._seen_pids = set()
            pics_file = os.path.join(self.path, 'pictures_id.txt')
            if os.path.isfile(pics_file):
                with open(pics_file, 'r', encoding='utf-8') as pf:
                    for line in pf:
                        self._seen_pids.add(line.strip())
        except Exception:
            self._seen_pids = set()
        self._pid_file_lock = threading.Lock()
        # collected pids reported by workers (commit by main thread)
        try:
            self._collected_pids = []
            self._collected_pids_lock = threading.Lock()
        except Exception:
            self._collected_pids = []
            self._collected_pids_lock = threading.Lock()

        pid_len = len(work_list)
        func = partial(self.thread_no_use_seleium_get_pid, self.cookies, self.Agent, self.path, '1')
        results = []
        # If single_mode_flag, run sequentially in this QThread to ensure sleeps/countdowns
        if self.single_mode_flag:
            try:
                self._output.emit("<p><font color='green'>已啟用超慢速(單執行緒) PID 模式</font></p>")
                self._output.emit("<p><font color='green'>等待區間: {} ~ {} 秒</font></p>".format(self.pid_wait_min, self.pid_wait_max))
            except Exception:
                pass
            for aid in work_list:
                if self._isPause == 2:
                    break
                try:
                    res = func(aid)
                    if isinstance(res, list):
                        results.append(res)
                except Exception as e:
                    try:
                        self._output.emit(f"<p><font color='red'>處理畫師 {aid} 發生錯誤: {e}</font></p>")
                    except Exception:
                        pass
                # 超慢速模式的固定延遲：每位畫師處理後都等待（成功/失敗都等）
                if self._isPause == 2:
                    break
                delay = pyrandom.randint(self.pid_wait_min, self.pid_wait_max)
                try:
                    self._output.emit(f"<p><font color='green'>開始等待 {delay} 秒 (畫師 {aid})</font></p>")
                except Exception:
                    pass
                # 直接在線程內 sleep，確保有實際休眠
                self._sleep_with_countdown(delay)
                try:
                    self._output.emit(f"<p><font color='green'>等待結束 (畫師 {aid})</font></p>")
                except Exception:
                    pass
        else:
            max_workers = 2
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as self.executor:
                results = list(self.executor.map(func, work_list))
        results=([i for item in results for i in item]) 
        #print(len(results))
        end=[i for i in results if i not in self.exist_pid]
        #print(len(end))
        # Commit collected per-artist progress updates once (single-writer)
        try:
            if hasattr(self, '_progress_updates') and self._progress_updates:
                try:
                    # reload existing progress (in case changed earlier)
                    prog = {}
                    if os.path.isfile(progress_file):
                        try:
                            with open(progress_file, 'r', encoding='utf-8') as pf:
                                prog = json.load(pf)
                        except Exception:
                            prog = {}
                    # apply updates (latest timestamp wins)
                    with self._progress_updates_lock:
                        for aid, ts in self._progress_updates:
                            prog[str(aid)] = ts
                    tmpfile = progress_file + '.tmp'
                    with open(tmpfile, 'w', encoding='utf-8') as pf:
                        json.dump(prog, pf, ensure_ascii=False, indent=2)
                    os.replace(tmpfile, progress_file)
                except Exception as e:
                    try:
                        self._output.emit(f"<p><font color='red'>無法寫入 progress: {e}</font></p>")
                    except Exception:
                        pass
        except Exception:
            pass
        # 主執行緒一次性寫入 pictures_id.txt，避免多執行緒同時寫檔
        try:
            pics_file = os.path.join(self.path, 'pictures_id.txt')
            try:
                with self._collected_pids_lock:
                    collected = list(self._collected_pids)
            except Exception:
                collected = list(getattr(self, '_collected_pids', []))

            existing_list = []
            if os.path.isfile(pics_file):
                try:
                    with open(pics_file, 'r', encoding='utf-8') as pf:
                        existing_list = [line.strip() for line in pf if line.strip()]
                except Exception:
                    existing_list = []

            existing_seen = set(existing_list)
            new_candidates = []
            for pid in (end + collected):
                spid = str(pid).strip()
                if not spid:
                    continue
                if spid in self.exist_pid:
                    continue
                if spid in existing_seen:
                    continue
                new_candidates.append(spid)
                existing_seen.add(spid)

            final_list = list(existing_list)
            final_list.extend(new_candidates)

            try:
                try:
                    atomic_write_text(pics_file, final_list)
                except Exception:
                    tmpfile = pics_file + '.tmp'
                    with open(tmpfile, 'w', encoding='utf-8') as pf:
                        for text in final_list:
                            pf.write(str(text) + '\n')
                    os.replace(tmpfile, pics_file)
            except Exception:
                try:
                    with open(pics_file, 'w', encoding='utf-8') as pf:
                        for text in final_list:
                            pf.write(str(text) + '\n')
                except Exception as e2:
                    try:
                        self._output.emit(f"<p><font color='red'>撖怠 pictures_id 憭望?: {e2}</font></p>")
                    except Exception:
                        pass

            try:
                self._output.emit(
                    "<p><font color='gray'>pictures_id 增量更新：舊 {} 筆，本次新增 {} 筆，總 {} 筆</font></p>".format(
                        len(existing_list), len(new_candidates), len(final_list)
                    )
                )
            except Exception:
                pass
        except Exception:
            pass
        if(self._isPause==2):
            self._finished.emit('已終止')
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
            self._output.emit("<p><font color='black'>正在抓取第"+str(pid_num)+"個畫師的圖片</font></p>")
        try:
            url='https://www.pixiv.net/ajax/user/'+author_pids+'/profile/all?lang=zh%27'
            headers = {
            'User-Agent': Agent,
            'Cookie':cookie
            ,'referer': 'https://www.pixiv.net/users/'+author_pids,        
            }
            res = requests.get(url, headers=headers)
            resdicts = res.json()['body']['illusts']
            pid=[_ for _ in resdicts]
            # filter out existing and already-seen pids for incremental append
            try:
                # 不在此階段處理 tag/like 過濾；僅將新 pid 報告到共享清單，由主執行緒一次性寫入檔案
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
                            self._output.emit(f"<p><font color='red'>收集 pictures_id 失敗: {e}</font></p>")
                        except Exception:
                            pass
            except Exception:
                pass
            # 報告進度到共享清單，由主執行緒一次性提交（避免檔案鎖衝突）
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
                    self._output.emit("<p><font color='red'>無法記錄 progress 更新至記憶體</font></p>")
                except Exception:
                    pass
            return pid
        except Exception as err:
            print(err)
            f = open((path+"authorPids_err"+str(num)+".txt"), "a+")
            f.write(author_pids+'\n')
            f.close() 
        

class get_img_url_thread(QThread):
    _signal = pyqtSignal(int,int)
    _output=pyqtSignal(str)
    _countdown = pyqtSignal(int)
    _finished = pyqtSignal(str)
    _thenext = pyqtSignal(int)
    pid_max=0
    pid_now=0
    _isPause=0
    path=os.getenv('APPDATA')+r'/pixiv_download/'
    def __init__(self,Author_list,Agent,cookies,exist_pid,ban_tag,must_tag,like_num,no_to_check, single_thread_mode=False, pid_wait_min=10, pid_wait_max=60, pid_wait_nocookie_min=None, pid_wait_nocookie_max=None):
        super(get_img_url_thread,self).__init__()
        self.Author_list=Author_list
        self.Agent=Agent
        self.cookies=cookies
        self.exist_pid=exist_pid
        self._isPause = 0
        self.cond = QWaitCondition()
        self.ban_tag=ban_tag
        self.must_tag=must_tag
        self.like_num=like_num
        self.no_to_check=no_to_check
        self.tag_queue=Queue()
        self.like_queue=Queue()
        self.single_mode_flag = bool(single_thread_mode)
        try:
            self.pid_wait_min = int(pid_wait_min)
            self.pid_wait_max = int(pid_wait_max)
        except Exception:
            self.pid_wait_min, self.pid_wait_max = 10, 60
        if self.pid_wait_min < 1:
            self.pid_wait_min = 1
        if self.pid_wait_max < self.pid_wait_min:
            self.pid_wait_max = self.pid_wait_min
        try:
            if pid_wait_nocookie_min is None:
                self.pid_wait_nocookie_min = max(0, int(round(self.pid_wait_min * 0.1)))
            else:
                self.pid_wait_nocookie_min = int(pid_wait_nocookie_min)
            if pid_wait_nocookie_max is None:
                self.pid_wait_nocookie_max = max(self.pid_wait_nocookie_min, int(round(self.pid_wait_max * 0.1)))
            else:
                self.pid_wait_nocookie_max = int(pid_wait_nocookie_max)
        except Exception:
            self.pid_wait_nocookie_min, self.pid_wait_nocookie_max = 1, 6
        if self.pid_wait_nocookie_min < 0:
            self.pid_wait_nocookie_min = 0
        if self.pid_wait_nocookie_max < self.pid_wait_nocookie_min:
            self.pid_wait_nocookie_max = self.pid_wait_nocookie_min
        self.url_meta = {}
        self.url_meta_path = os.path.join(self.path, "all_url_meta.json")
        self._pid_cache_hit = {}
        self._cookie_requirement_map = {}
        try:
            if os.path.isfile(self.url_meta_path):
                with open(self.url_meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    self.url_meta = meta
        except Exception:
            self.url_meta = {}
        try:
            req_path = os.path.join(self.path, 'pixiv_cookie_requirement.json')
            if os.path.isfile(req_path):
                with open(req_path, 'r', encoding='utf-8') as f:
                    req_data = json.load(f)
                if isinstance(req_data, dict):
                    for _pid, _entry in req_data.items():
                        if isinstance(_entry, dict):
                            self._cookie_requirement_map[str(_pid)] = _entry.get('requires_cookie')
        except Exception:
            self._cookie_requirement_map = {}
        #print(self.no_to_check)

    def _sleep_ultra_slow(self, pid, need_cookie=None):
        if not self.single_mode_flag:
            return
        if need_cookie is False:
            delay = pyrandom.randint(self.pid_wait_nocookie_min, self.pid_wait_nocookie_max)
        else:
            delay = pyrandom.randint(self.pid_wait_min, self.pid_wait_max)
        try:
            ratio_text = "no-cookie" if need_cookie is False else "cookie"
            self._output.emit("<p><font color='green'>[URL階段] 開始等待 {} 秒 (PID {}, 模式 {})</font></p>".format(delay, pid, ratio_text))
        except Exception:
            pass
        for _ in range(delay):
            if self._isPause == 2:
                break
            while self._isPause == 1:
                time.sleep(1)
            try:
                self._countdown.emit(delay - _)
            except Exception:
                pass
            time.sleep(1)
        try:
            self._countdown.emit(0)
        except Exception:
            pass
        try:
            self._output.emit("<p><font color='green'>[URL階段] 等待結束 (PID {})</font></p>".format(pid))
        except Exception:
            pass

    def _extract_pid_from_url(self, url):
        try:
            filename = str(url).rsplit('/', 1)[1]
            pid_token = str(filename.split('_', 1)[0])
            m = re.match(r"^(\d+)", pid_token)
            return m.group(1) if m else pid_token
        except Exception:
            return None

    def __del__(self):
        try:
            executor = getattr(self, 'executor', None)
            if executor is not None:
                executor.shutdown(wait=False)
        except Exception:
            pass
        self.wait()

    def _flush_url_meta_snapshot(self):
        try:
            with open(self.url_meta_path, 'w', encoding='utf-8') as f:
                json.dump(self.url_meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _write_all_url_snapshot(self, fetched_urls):
        """分批寫入 all_url.txt，讓長流程中也能持續看到更新。"""
        try:
            file_path = self.path
            file_name = "all_url.txt"
            old_urls = []
            try:
                with open(file_path + "/" + file_name, "r", encoding='utf-8') as f:
                    old_urls = [line.rstrip() for line in f if line.rstrip()]
            except Exception:
                old_urls = []

            def _keep_not_downloaded(url):
                pid = self._extract_pid_from_url(url)
                return (pid is None) or (pid not in self.exist_pid)

            def _normalize_tags(tags):
                if isinstance(tags, list):
                    return [str(t).strip() for t in tags if str(t).strip()]
                if tags is None:
                    return []
                s = str(tags).strip()
                return [s] if s else []

            def _like_to_int(v):
                try:
                    if isinstance(v, bool):
                        return 0
                    return int(v)
                except Exception:
                    try:
                        return int(str(v).strip())
                    except Exception:
                        return 0

            try:
                ban_rules = tag_edit.Tag(self.ban_tag)
            except Exception:
                ban_rules = []
            try:
                must_rules = tag_edit.Tag(self.must_tag)
            except Exception:
                must_rules = []
            try:
                like_threshold = int(self.like_num)
            except Exception:
                like_threshold = 0

            filtered_tag = set()
            filtered_like = set()

            def _keep_by_cached_rules(url):
                pid = self._extract_pid_from_url(url)
                if not pid:
                    return True
                meta = self.url_meta.get(str(pid), {}) if isinstance(self.url_meta, dict) else {}
                if not isinstance(meta, dict) or not meta:
                    # 沒有快取資料時不強制過濾，避免誤刪
                    return True

                tags = _normalize_tags(meta.get('tag', []))
                tag_blob = ' '.join(tags)

                for b in ban_rules:
                    if b and (b in tag_blob):
                        filtered_tag.add(str(pid))
                        return False

                if must_rules:
                    ok = any((m and (m in tag_blob)) for m in must_rules)
                    if not ok:
                        filtered_tag.add(str(pid))
                        return False

                like_i = _like_to_int(meta.get('like', 0))
                if like_i < like_threshold:
                    filtered_like.add(str(pid))
                    return False

                return True

            old_urls = [u for u in old_urls if _keep_not_downloaded(u) and _keep_by_cached_rules(u)]
            new_urls = [u for u in fetched_urls if isinstance(u, str) and ('https' in u) and _keep_not_downloaded(u) and _keep_by_cached_rules(u)]

            # 將二次過濾結果回寫到既有記錄，供 pass_tag/pass_like 使用
            try:
                for p in filtered_tag:
                    self.tag_queue.put(p)
                for p in filtered_like:
                    self.like_queue.put(p)
                if filtered_tag or filtered_like:
                    self._output.emit("<p><font color='gray'>[URL二次檢查] 依快取過濾：tag {} 筆，like {} 筆</font></p>".format(len(filtered_tag), len(filtered_like)))
            except Exception:
                pass

            merged = []
            seen = set()
            for u in old_urls + new_urls:
                if u in seen:
                    continue
                seen.add(u)
                merged.append(u)

            try:
                os.makedirs(file_path, exist_ok=True)
            except Exception:
                pass
            # all_url.txt 和 all_url_meta.json 都需要備份
            try:
                atomic_write_text(os.path.join(file_path, file_name), merged, backup=True)
            except Exception:
                try:
                    with open(file_path + "/" + file_name, "w+", encoding='utf-8') as f:
                        f.writelines([str(text) + "\n" for text in merged])
                except Exception:
                    pass
            return old_urls, new_urls, merged
        except Exception:
            return [], [], []

    def pause(self):
        self._output.emit("<p><font color='red'>暫停</font></p>")
        self._isPause = 1
        self._flush_url_meta_snapshot()

    def resume(self):
        self._output.emit("<p><font color='red'>繼續</font></p>")
        self._isPause = 0
        
    def stop(self):
        self._output.emit("<p><font color='red'>中止</font></p>")
        self._isPause = 2 
        self._flush_url_meta_snapshot()
    def check_exist(self):
        pid_path = os.path.join(self.path, "pictures_id.txt")
        def _read_pid_file(path):
            with open(path, encoding='utf-8') as file:
                return [line.rstrip() for line in file if line.rstrip() and line.rstrip() not in self.no_to_check]
        try:
            return _read_pid_file(pid_path)
        except Exception:
            # 先嘗試從最近備份復原 pictures_id.txt
            try:
                bak_files = sorted(glob.glob(pid_path + '.bak_*'), key=lambda p: os.path.getmtime(p), reverse=True)
                if bak_files:
                    shutil.copy2(bak_files[0], pid_path)
                    self._output.emit("<p><font color='orange'>pictures_id.txt 讀取失敗，已從備份自動復原</font></p>")
                    return _read_pid_file(pid_path)
            except Exception:
                pass

            # 再嘗試由 all_url_meta.json 重建 PID 清單
            try:
                meta_path = os.path.join(self.path, 'all_url_meta.json')
                if os.path.isfile(meta_path):
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    if isinstance(meta, dict):
                        rebuilt = [str(k) for k in meta.keys() if str(k).strip()]
                        if rebuilt:
                            try:
                                atomic_write_text(pid_path, rebuilt, backup=True)
                            except Exception:
                                with open(pid_path, 'w', encoding='utf-8') as wf:
                                    for p in rebuilt:
                                        wf.write(str(p) + '\n')
                            self._output.emit("<p><font color='orange'>pictures_id.txt 不存在，已由 all_url_meta.json 自動重建</font></p>")
                            return [p for p in rebuilt if p not in self.no_to_check]
            except Exception:
                pass

            self._finished.emit('找不到P站畫師文檔請重新執行上一步')
            self._thenext.emit(-1)
            return 0
    def run(self):
        try:
            self._output.emit("URL階段開始")
            pictures_id = self.check_exist()
            if not isinstance(pictures_id, list):
                self._output.emit("<p><font color='red'>pictures_id 讀取失敗，已停止 URL 階段</font></p>")
                self._thenext.emit(-1)
                return
            self.pid_max = len(pictures_id)
            self._output.emit("<p><font color='red'>一共"+str(self.pid_max)+"張抓取到的PID</font></p>")
            try:
                self._output.emit("<p><font color='gray'>URL 目標檔案: {}</font></p>".format(os.path.join(self.path, "all_url.txt")))
            except Exception:
                pass
            if self.pid_max == 0:
                self._output.emit("<p><font color='orange'>pictures_id.txt 目前為空，沒有可轉換的 PID</font></p>")
                self._thenext.emit(4)
                return
            if self.single_mode_flag:
                self._output.emit("<p><font color='green'>URL階段已啟用超慢速模式，cookie等待: {}~{} 秒；非cookie等待: {}~{} 秒</font></p>".format(self.pid_wait_min, self.pid_wait_max, self.pid_wait_nocookie_min, self.pid_wait_nocookie_max))
            results = []
            for idx, i in enumerate(pictures_id, start=1):
                if idx % 20 == 1:
                    try:
                        self._output.emit("<p><font color='black'>URL階段進度：{}/{} (PID {})</font></p>".format(idx, self.pid_max, i))
                    except Exception:
                        pass
                one = self.get_download_url(self.path, self.Agent, 1, i)
                if isinstance(one, list):
                    results.append(one)
                elif isinstance(one, str):
                    # 僅保留可辨識結果，避免後續扁平化把字串拆成單字元
                    if one.startswith('http'):
                        results.append([one])
                    else:
                        results.append([])
                if idx % 20 == 0:
                    flat_results = [x for item in results if isinstance(item, list) for x in item]
                    old_urls, new_urls, merged = self._write_all_url_snapshot(flat_results)
                    self._flush_url_meta_snapshot()
                    try:
                        self._output.emit("<p><font color='gray'>[分批寫入] 已處理 {}/{}，all_url 目前 {} 筆（本批新增 {}）</font></p>".format(idx, self.pid_max, len(merged), len(new_urls)))
                    except Exception:
                        pass
                if self._isPause == 2:
                    break
            # func=partial(self.get_download_url,self.path,self.Agent,1) 
            # with concurrent.futures.ThreadPoolExecutor(max_workers=1) as self.executor:
            #     results = list(self.executor.map(func, pictures_id))
            if self._isPause==2:
                try:
                    flat_results = [x for item in results if isinstance(item, list) for x in item]
                    old_urls, new_urls, merged = self._write_all_url_snapshot(flat_results)
                    self._flush_url_meta_snapshot()
                    self._output.emit("<p><font color='orange'>已中止；已保存目前 all_url {} 筆（本次新增 {}）</font></p>".format(len(merged), len(new_urls)))
                except Exception:
                    pass
                self._finished.emit('已終止')
                self._thenext.emit(-1)
            else:
                results = [i for item in results if isinstance(item, list) for i in item]
                #print(results)
                error_pid=[i for i in results if 'https' not in i]
                results=[i for i in results if 'https' in i]
                old_urls, new_urls, merged = self._write_all_url_snapshot(results)

                try:
                    self._output.emit("<p><font color='green'>all_url 寫入完成：舊URL {} 筆、新URL {} 筆、合併後 {} 筆</font></p>".format(len(old_urls), len(new_urls), len(merged)))
                except Exception:
                    pass

                if len(new_urls) == 0:
                    self._output.emit("<p><font color='gray'>URL佇列未新增新資料，沿用既有 all_url.txt</font></p>")
                try:
                    try:
                        atomic_write_json(self.url_meta_path, self.url_meta)
                    except Exception:
                        with open(self.url_meta_path, 'w', encoding='utf-8') as f:
                            json.dump(self.url_meta, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    self._output.emit("<p><font color='red'>寫入 all_url_meta.json 失敗: {}</font></p>".format(e))
                file_path = self.path
                file_name = "tag_ban_pid.txt"     
                tag_err = [self.tag_queue.get() for _ in range(self.tag_queue.qsize())]
                
                try:
                    from safe_io import atomic_append_text
                    atomic_append_text(os.path.join(file_path, file_name), tag_err)
                except Exception:
                    try:
                        with open(file_path + "/" + file_name, "a+") as f:
                            f.writelines([str(text) + "\n" for text in tag_err])
                    except Exception:
                        pass
                    
                file_name = "pid_num_pid.txt"     
                like_err = [self.like_queue.get() for _ in range(self.like_queue.qsize())]
                
                try:
                    from safe_io import atomic_append_text
                    atomic_append_text(os.path.join(file_path, file_name), like_err)
                except Exception:
                    try:
                        with open(file_path + "/" + file_name, "a+") as f:
                            f.writelines([str(text) + "\n" for text in like_err])
                    except Exception:
                        pass

                try:
                    from safe_io import atomic_write_text
                    atomic_write_text(os.path.join(self.path, "net_err.txt"), [str(text) for text in error_pid])
                except Exception:
                    try:
                        f = open((self.path+"/net_err"+".txt"), "w+")     #讀取寫入的文檔
                        for text in error_pid:
                            f.write(str(text)+'\n')
                        f.close()
                    except Exception:
                        pass
                self._finished.emit('抓取所有PID完成')
                self._thenext.emit(4)
                self._output.emit("<p><font color='red'>一共"+str(len(merged))+"張待下載圖片 </font></p>")
        except Exception as e:
            self._output.emit('獲得關注失敗 以下為報錯訊息')
            self._output.emit(output_err(e))
            self._thenext.emit(-1)
    def get_download_url(self,path,Agent,num,pid):    #回傳下載連結
        while (self._isPause==1):
            time.sleep(1)
            #print('wait for singal')
        if self._isPause==2:
            return []
        should_wait = False

        def _normalize_tags(tags):
            if isinstance(tags, list):
                return [str(t).strip() for t in tags if str(t).strip()]
            if tags is None:
                return []
            s = str(tags).strip()
            return [s] if s else []

        def _like_to_int(v):
            try:
                if isinstance(v, bool):
                    return 0
                return int(v)
            except Exception:
                try:
                    return int(str(v).strip())
                except Exception:
                    return 0

        def _apply_filters(_pid, _tag, _like):
            # ban_tag: 命中任一關鍵字即跳過
            tag_list = _normalize_tags(_tag)
            tag_blob = ' '.join(tag_list)
            try:
                for b in tag_edit.Tag(self.ban_tag):
                    if b and (b in tag_blob):
                        self._output.emit("<p><font color='black'>因檢測到包含 {} 的TAG，PID {} 跳過</font></p>".format(b, _pid))
                        self.tag_queue.put(str(_pid))
                        return False
            except Exception:
                pass

            # must_tag: 未命中任一必要標籤即跳過
            try:
                musts = tag_edit.Tag(self.must_tag)
            except Exception:
                musts = []
            if musts:
                ok = any((m and (m in tag_blob)) for m in musts)
                if not ok:
                    self._output.emit("<p><font color='black'>未包含必要TAG，PID {} 跳過</font></p>".format(_pid))
                    self.tag_queue.put(str(_pid))
                    return False

            # like 門檻：低於設定值則跳過
            like_i = _like_to_int(_like)
            try:
                threshold = int(self.like_num)
            except Exception:
                threshold = 0
            if like_i < threshold:
                self._output.emit("<p><font color='black'>愛心太少了 {}，未達預設 {}，PID {} 跳過</font></p>".format(like_i, threshold, _pid))
                self.like_queue.put(str(_pid))
                return False

            return True

        if str(pid) in self.exist_pid:
            try:
                self._output.emit("<p><font color='gray'>PID {} 已存在，URL階段跳過</font></p>".format(pid))
            except Exception:
                pass
            if self._isPause!=2:
                self.pid_now=self.pid_now+1
                self._signal.emit(1,self.pid_max-1)
            return []
        download_url=[]
        url='https://www.pixiv.net/artworks/'+pid
        # 先吃本地快取，避免重複查詢
        cached = self.url_meta.get(str(pid)) if isinstance(self.url_meta, dict) else None
        if isinstance(cached, dict) and cached.get('img_url') not in (None, 'None', '') and int(cached.get('pagecount', 0) or 0) > 0:
            self._pid_cache_hit[str(pid)] = True
            tag = cached.get('tag', [])
            like = cached.get('like', 0)
            pagecount = int(cached.get('pagecount', 1) or 1)
            img_url = cached.get('img_url')
            need_cookie = cached.get('requires_cookie', None)
            try:
                self._output.emit("<p><font color='green'>PID {} 使用本地快取資料</font></p>".format(pid))
            except Exception:
                pass
            if self.single_mode_flag:
                try:
                    self._output.emit("<p><font color='gray'>[URL階段] PID {} 使用本地快取，跳過等待</font></p>".format(pid))
                except Exception:
                    pass
            if not _apply_filters(pid, tag, like):
                if self.single_mode_flag and should_wait:
                    self._sleep_ultra_slow(pid, need_cookie=need_cookie)
                if self._isPause!=2:
                    self.pid_now=self.pid_now+1
                    self._signal.emit(1,self.pid_max-1)
                return []
        else:
            self._pid_cache_hit[str(pid)] = False
            should_wait = True
            try:
                info = Pixiv_info(url,Agent=Agent,cookie=self.cookies)
            except Exception as e:
                try:
                    self._output.emit("<p><font color='red'>PID {} 取得資訊失敗：{}</font></p>".format(pid, e))
                except Exception:
                    pass
                if self.single_mode_flag and should_wait:
                    self._sleep_ultra_slow(pid, need_cookie=None)
                if self._isPause!=2:
                    self.pid_now=self.pid_now+1
                    self._signal.emit(1,self.pid_max-1)
                return [str(pid)]
            if info == [404]:
                if self.single_mode_flag and should_wait:
                    self._sleep_ultra_slow(pid, need_cookie=None)
                if self._isPause!=2:
                    self.pid_now=self.pid_now+1
                    self._signal.emit(1,self.pid_max-1)
                return [str(pid)]
            try:
                tag,like,pagecount,img_url = info
            except Exception:
                if self.single_mode_flag and should_wait:
                    self._sleep_ultra_slow(pid, need_cookie=None)
                if self._isPause!=2:
                    self.pid_now=self.pid_now+1
                    self._signal.emit(1,self.pid_max-1)
                return [str(pid)]
            if not _apply_filters(pid, tag, like):
                if self.single_mode_flag and should_wait:
                    self._sleep_ultra_slow(pid, need_cookie=None)
                if self._isPause!=2:
                    self.pid_now=self.pid_now+1
                    self._signal.emit(1,self.pid_max-1)
                return []
            try:
                need_cookie = self._cookie_requirement_map.get(str(pid), None)
            except Exception:
                need_cookie = None
        try:
            if need_cookie is True:
                self._output.emit("<p><font color='blue'>PID {} 需要 cookies 才能取得完整資料</font></p>".format(pid))
            elif need_cookie is False:
                self._output.emit("<p><font color='gray'>PID {} 不需要 cookies</font></p>".format(pid))
        except Exception:
            need_cookie = None

        try:
            self.url_meta[str(pid)] = {
                "tag": tag if isinstance(tag, list) else [],
                "like": int(like) if str(like).isdigit() else like,
                "pagecount": int(pagecount) if str(pagecount).isdigit() else pagecount,
                "img_url": img_url,
                "requires_cookie": need_cookie,
                "artwork_url": url,
            }
        except Exception:
            pass

        if not img_url or str(img_url) == 'None':
            if self.single_mode_flag and should_wait:
                self._sleep_ultra_slow(pid, need_cookie=need_cookie)
            if self._isPause!=2:
                self.pid_now=self.pid_now+1
                self._signal.emit(1,self.pid_max-1)
            return [str(pid)]
        try:
            img = str(img_url).rsplit(".",1)
            page_total = int(pagecount) if int(pagecount) > 0 else 1
            for count in range(0, page_total):
                download_url.append(img[0]+str(count)+"."+img[1])
        except Exception:
            if self.single_mode_flag and should_wait:
                self._sleep_ultra_slow(pid, need_cookie=need_cookie)
            if self._isPause!=2:
                self.pid_now=self.pid_now+1
                self._signal.emit(1,self.pid_max-1)
            return [str(pid)]
        if self.single_mode_flag and should_wait:
            self._sleep_ultra_slow(pid, need_cookie=need_cookie)
        if self._isPause!=2:
            self.pid_now=self.pid_now+1
            self._signal.emit(1,self.pid_max-1)

        # for x in range(0,2):
        #     try:
        #         #print('檢測tag')
        #         url='https://www.pixiv.net/artworks/'+pid
        #         #print(url)
        #         j=1
        #         while(j < 3):
        #             tag,like,pagecount,img_url=Pixiv_info(url,Agent=Agent,cookie=self.cookies)
        #             print(img_url)
        #             j = j + 1
        #             if tag != [] or like != 404:
        #                 break
        #             if tag == 404 and like == 404:
        #                 break
        #         if j == 3:
        #             raise Exception()
        #         if tag ==404 and like==404:
        #             break
        #         tag=str(tag) 
        #         self.ban_tag=tag_edit.Tag(self.ban_tag)
        #         for i in self.ban_tag:
        #             if i in tag:
        #                 info="<p><font color='black'>因檢測到包含 {} 的TAG PID為 {} 跳過</font></p>"
        #                 self._output.emit(info.format(i, pid))
        #                 self.tag_queue.put(pid)
        #                 return ['0']
        #         self.must_tag=tag_edit.Tag(self.must_tag)
        #         if self.must_tag!=[]:
        #             ok_status=0
        #             for i in self.must_tag:
        #                 if i in tag:   
        #                     ok_status=1
        #                     break
        #             if ok_status==0:
        #                 self._output.emit("<p><font color='black'>檢測到未包含使用者要求的TAG PID為"+pid+"跳過</font></p>")
        #                 self.tag_queue.put(pid)
        #                 return ['0']
        #         if like <self.like_num:
        #             #print('愛心太少了'+str(like)+'未達預設 PID為 '+ pid)
        #             self._output.emit("<p><font color='black'>愛心太少了"+str(like)+"未達預設 PID為 "+pid+"</font></p>")
        #             if int(pid)<94006000:
        #                 self.like_queue.put(pid)
        #             return ['0']     
        #         img_url=img_url.rsplit(".",1)
        #         for count in range(0,pagecount):
        #             download_url.append(img_url[0]+str(count)+"."+img_url[1])
        #         time.sleep(random()/5)
        #         return (download_url)   
        #     except Exception as err:
        #         output_err(err)
        #         print(pid+'獲取失敗',err) 
        #         if x==9:
        #                 print(pid+'獲取失敗9次',err) 
        #                 myfile = Path(path+"network_err"+str(num%20)+".txt")
        #                 myfile.touch(exist_ok=True)
        #                 f = open((path+"network_err"+str(num%20)+".txt"), "r")           
        #                 exist=f.read()
        #                 f.close()
        #                 if str(pid) not in exist:
        #                     f = open((path+"network_err"+str(num%20)+".txt"), "a+")  
        #                     f.write(str(pid)+'\n')
        #                     f.close() 
            #time.sleep(0.5+random()/10)
        return(download_url)

class download_thread(QThread):
    _signal = pyqtSignal(int,int)
    _output=pyqtSignal(str)
    _countdown = pyqtSignal(int)
    _finished = pyqtSignal(str)
    _timechanged=pyqtSignal(str)
    _thenext = pyqtSignal(int)
    pid_max=0
    pid_now=0
    _isPause=0
    path=os.getenv('APPDATA')+r'/pixiv_download/'
    timelock = QMutex()
    def __init__(self,nogif,notag,notime,create_dir,download_path,cookies,agent,download_time,no_R18G_dir, like_threshold=0, jxl_enable=False, jxl_cjxl_path=r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe", jxl_delete_original=False, jxl_effort=7, single_thread_mode=False, download_wait_min=10, download_wait_max=60, intra_pid_wait_min=1, intra_pid_wait_max=3, download_wait_nocookie_min=None, download_wait_nocookie_max=None, intra_pid_wait_nocookie_min=None, intra_pid_wait_nocookie_max=None):
        super(download_thread,self).__init__()
        self.nogif=nogif
        self.notime=notime
        self.notag=notag
        self.create_dir=create_dir
        self.download_path=download_path     
        self.cookies=cookies
        self.agent=agent
        self.download_time=download_time
        self.no_R18G_dir=no_R18G_dir
        try:
            self.like_threshold = max(0, int(like_threshold))
        except Exception:
            self.like_threshold = 0
        self.jxl_enable = bool(jxl_enable)
        self.jxl_cjxl_path = str(jxl_cjxl_path).strip() if jxl_cjxl_path else r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe"
        self.jxl_delete_original = bool(jxl_delete_original)
        try:
            self.jxl_effort = min(9, max(1, int(jxl_effort)))
        except Exception:
            self.jxl_effort = 7
        self.single_thread_mode = single_thread_mode
        # explicit local flag for clarity elsewhere in code
        self.single_mode_flag = bool(single_thread_mode)
        try:
            self.download_wait_min = int(download_wait_min)
            self.download_wait_max = int(download_wait_max)
        except Exception:
            self.download_wait_min, self.download_wait_max = 10, 60
        if self.download_wait_min < 0:
            self.download_wait_min = 0
        if self.download_wait_max < self.download_wait_min:
            self.download_wait_max = self.download_wait_min
        try:
            if download_wait_nocookie_min is None:
                self.download_wait_nocookie_min = max(0, int(round(self.download_wait_min * 0.1)))
            else:
                self.download_wait_nocookie_min = int(download_wait_nocookie_min)
            if download_wait_nocookie_max is None:
                self.download_wait_nocookie_max = max(self.download_wait_nocookie_min, int(round(self.download_wait_max * 0.1)))
            else:
                self.download_wait_nocookie_max = int(download_wait_nocookie_max)
        except Exception:
            self.download_wait_nocookie_min, self.download_wait_nocookie_max = 0, 1
        if self.download_wait_nocookie_min < 0:
            self.download_wait_nocookie_min = 0
        if self.download_wait_nocookie_max < self.download_wait_nocookie_min:
            self.download_wait_nocookie_max = self.download_wait_nocookie_min
        try:
            self.intra_pid_wait_min = int(intra_pid_wait_min)
            self.intra_pid_wait_max = int(intra_pid_wait_max)
        except Exception:
            self.intra_pid_wait_min, self.intra_pid_wait_max = 1, 3
        if self.intra_pid_wait_min < 0:
            self.intra_pid_wait_min = 0
        if self.intra_pid_wait_max < self.intra_pid_wait_min:
            self.intra_pid_wait_max = self.intra_pid_wait_min
        try:
            if intra_pid_wait_nocookie_min is None:
                self.intra_pid_wait_nocookie_min = max(0, int(round(self.intra_pid_wait_min * 0.1)))
            else:
                self.intra_pid_wait_nocookie_min = int(intra_pid_wait_nocookie_min)
            if intra_pid_wait_nocookie_max is None:
                self.intra_pid_wait_nocookie_max = max(self.intra_pid_wait_nocookie_min, int(round(self.intra_pid_wait_max * 0.1)))
            else:
                self.intra_pid_wait_nocookie_max = int(intra_pid_wait_nocookie_max)
        except Exception:
            self.intra_pid_wait_nocookie_min, self.intra_pid_wait_nocookie_max = 0, 1
        if self.intra_pid_wait_nocookie_min < 0:
            self.intra_pid_wait_nocookie_min = 0
        if self.intra_pid_wait_nocookie_max < self.intra_pid_wait_nocookie_min:
            self.intra_pid_wait_nocookie_max = self.intra_pid_wait_nocookie_min
        self.url_meta = {}
        self.url_meta_path = os.path.join(self.path, "all_url_meta.json")
        self.like_less_path = os.path.join(self.path, "like_less.txt")
        self.allurl = []
        self.exist_json_path = os.path.join(self.path, "exist_pid.json")
        self.legacy_exist_json_path = os.path.join(self.path, "exist.json")
        self.exist_txt_path = os.path.join(self.path, "existPID.txt")
        
        self.q=Queue()
        self._stop_after_group = False
        self._stopped_by_request = False
        self._active_group_pid = None
        self._attempted_urls = set()
        self._attempted_urls_lock = threading.Lock()
        self._pid_cookie_used = {}
        self._pid_like_cache = {}
        self._permanent_skip_pids = set()
        self._like_less_set = set()
        self._min_free_bytes = 200 * 1024 * 1024  # 至少保留 200MB 緩衝
        self._no_space_alerted = False
        if not os.path.exists(self.download_path):
            os.mkdir(self.download_path)
        try:
            base_exist = set()
            if os.path.isfile(self.exist_json_path):
                with open(self.exist_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    base_exist = set(str(x).replace('p0', '') for x in data)
            elif os.path.isfile(self.legacy_exist_json_path):
                # 相容舊檔 exist.json
                with open(self.legacy_exist_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    base_exist = set(str(x).replace('p0', '') for x in data)
            elif os.path.isfile(self.exist_txt_path):
                with open(self.exist_txt_path, encoding='utf-8') as file:     # 舊格式相容
                    base_exist = set(line.rstrip().replace('p0', '') for line in file if line.rstrip())
            self.exist_pid = base_exist.union(set(self.splitID(self.get_filelist(self.download_path))))
        except Exception:
            self.exist_pid = set(self.splitID(self.get_filelist(self.download_path)))

        try:
            if os.path.isfile(self.url_meta_path):
                with open(self.url_meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    self.url_meta = meta
        except Exception:
            self.url_meta = {}

        try:
            if os.path.isfile(self.like_less_path):
                with open(self.like_less_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        s = str(line).strip()
                        if not s:
                            continue
                        token = s.split()[0]
                        m = re.match(r"^(\d+)", token)
                        pid = m.group(1) if m else token
                        if pid:
                            self._like_less_set.add(str(pid))
        except Exception:
            self._like_less_set = set()

        try:
            with open((self.path+r"/all_url.txt")) as file:     #讀取寫入的文檔
                self.allurl = [line.rstrip() for line in file if line.rstrip()]
        except Exception:
            # 嘗試由 all_url_meta.json 重建下載連結
            rebuilt = []
            try:
                for pid, m in self.url_meta.items():
                    if not isinstance(m, dict):
                        continue
                    img_url = m.get('img_url')
                    pagecount = int(m.get('pagecount', 1) or 1)
                    if not img_url or str(img_url) == 'None':
                        continue
                    if '.' not in str(img_url):
                        rebuilt.append(str(img_url))
                        continue
                    left, right = str(img_url).rsplit('.', 1)
                    for i in range(max(pagecount, 1)):
                        rebuilt.append(f"{left}{i}.{right}")
            except Exception:
                rebuilt = []
            self.allurl = rebuilt
            if not self.allurl:
                self._finished.emit('找不到下載連結的檔案\n請重新執行上一步')
        self.pid_max=len(self.allurl)
        print(self.pid_max)   

    def _normalize_pixiv_info(self, info):
        """將 Pixiv_info 結果正規化為 (tag, like, pagecount, img_url)，失敗回傳 None。"""
        try:
            if isinstance(info, list) and len(info) >= 4:
                tag = info[0] if isinstance(info[0], list) else []
                like = info[1]
                pagecount = info[2]
                img_url = info[3]
                return tag, like, pagecount, img_url
        except Exception:
            pass
        return None

    def _safe_int(self, value, default=None):
        try:
            if isinstance(value, bool):
                return int(value)
            return int(value)
        except Exception:
            try:
                return int(float(str(value).strip()))
            except Exception:
                return default

    def _build_cjxl_command(self, src_path, dst_path):
        ext = str(src_path).lower().rsplit('.', 1)[-1] if '.' in str(src_path) else ''
        cmd = [self.jxl_cjxl_path, str(src_path), str(dst_path), '--effort', str(self.jxl_effort)]
        if ext in ('jpg', 'jpeg'):
            cmd.append('--lossless_jpeg=1')
        else:
            cmd.append('--distance=0')
        return cmd

    def _convert_file_to_jxl_lossless(self, src_path):
        if not self.jxl_enable:
            return
        try:
            def _fmt_size(num_bytes):
                try:
                    b = float(num_bytes)
                except Exception:
                    return str(num_bytes)
                if b >= 1024 * 1024:
                    return "{:.2f} MB".format(b / 1024 / 1024)
                if b >= 1024:
                    return "{:.2f} KB".format(b / 1024)
                return "{} B".format(int(b))

            source = Path(src_path)
            if (not source.exists()) or (not source.is_file()):
                return
            if source.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                return
            if not self.jxl_cjxl_path or (not os.path.isfile(self.jxl_cjxl_path)):
                self._output.emit("<p><font color='orange'>JXL: cjxl 路徑無效，略過轉檔：{}</font></p>".format(self.jxl_cjxl_path))
                return

            dst = source.with_suffix('.jxl')
            if dst.exists():
                if self.jxl_delete_original:
                    try:
                        source.unlink(missing_ok=True)
                    except Exception:
                        pass
                return

            cmd = self._build_cjxl_command(source, dst)
            completed = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if completed.returncode == 0 and dst.exists():
                self._output.emit("<p><font color='green'>JXL 轉檔成功：{}</font></p>".format(dst.name))
                try:
                    src_size = int(source.stat().st_size)
                    jxl_size = int(dst.stat().st_size)
                    delta = src_size - jxl_size
                    if src_size > 0:
                        pct = (delta / src_size) * 100.0
                    else:
                        pct = 0.0
                    if delta >= 0:
                        self._output.emit("<p><font color='gray'>JXL 壓縮：{} -> {}，減少 {} ({:.2f}%)</font></p>".format(_fmt_size(src_size), _fmt_size(jxl_size), _fmt_size(delta), pct))
                    else:
                        self._output.emit("<p><font color='orange'>JXL 大小：{} -> {}，增加 {} ({:.2f}%)</font></p>".format(_fmt_size(src_size), _fmt_size(jxl_size), _fmt_size(-delta), -pct))
                except Exception:
                    pass
                if self.jxl_delete_original:
                    try:
                        source.unlink(missing_ok=True)
                        self._output.emit("<p><font color='gray'>JXL: 已刪除原檔 {}</font></p>".format(source.name))
                    except Exception:
                        pass
                return

            reason = (completed.stderr or completed.stdout or '').strip()
            if not reason:
                reason = 'cjxl failed'
            self._output.emit("<p><font color='orange'>JXL 轉檔失敗：{} ({})</font></p>".format(source.name, reason[:200]))
        except Exception as e:
            try:
                self._output.emit("<p><font color='orange'>JXL 轉檔例外：{} ({})</font></p>".format(src_path, e))
            except Exception:
                pass

    def _get_pid_like(self, pid):
        pid = str(pid)
        if pid in self._pid_like_cache:
            return self._pid_like_cache.get(pid)
        like_value = None
        try:
            meta = self.url_meta.get(pid, {}) if isinstance(self.url_meta, dict) else {}
            if isinstance(meta, dict):
                like_value = self._safe_int(meta.get('like', None), default=None)
        except Exception:
            like_value = None
        if like_value is None:
            try:
                info = pixiv_api.Pixiv_info('https://www.pixiv.net/artworks/' + pid, self.agent, cookie=self.cookies)
                normalized = self._normalize_pixiv_info(info)
                if normalized:
                    tag, like, pagecount, img_url = normalized
                    like_value = self._safe_int(like, default=None)
                    try:
                        if isinstance(self.url_meta, dict):
                            self.url_meta[pid] = {
                                "tag": tag if isinstance(tag, list) else [],
                                "like": like_value if like_value is not None else like,
                                "pagecount": self._safe_int(pagecount, default=pagecount),
                                "img_url": img_url,
                                "requires_cookie": self.url_meta.get(pid, {}).get('requires_cookie', None) if isinstance(self.url_meta.get(pid, {}), dict) else None,
                                "artwork_url": 'https://www.pixiv.net/artworks/' + pid,
                            }
                    except Exception:
                        pass
            except Exception:
                like_value = None
        self._pid_like_cache[pid] = like_value
        return like_value

    def _record_like_less_pid(self, pid, like_value):
        pid = str(pid)
        if pid in self._like_less_set:
            return
        self._like_less_set.add(pid)
        self._permanent_skip_pids.add(pid)
        line = "{} like<300 (like={})".format(pid, like_value)
        try:
            try:
                from safe_io import atomic_append_text
                atomic_append_text(self.like_less_path, line)
            except Exception:
                with open(self.like_less_path, 'a+', encoding='utf-8') as f:
                    f.write(line + '\n')
            self._output.emit("<p><font color='gray'>PID {} 已加入 like_less.txt（後續自動排除）</font></p>".format(pid))
        except Exception:
            pass

    def _pass_like_threshold(self, pid):
        pid = str(pid)
        if pid in self._like_less_set:
            self._permanent_skip_pids.add(pid)
            try:
                self._output.emit("<p><font color='black'>PID {} 位於 like_less.txt（<300），下載階段自動跳過</font></p>".format(pid))
            except Exception:
                pass
            return False
        threshold = self._safe_int(self.like_threshold, default=0)
        if not threshold or threshold <= 0:
            return True
        like_value = self._get_pid_like(pid)
        if like_value is None:
            try:
                self._output.emit("<p><font color='orange'>PID {} 無法確認愛心數，先略過（保留在待下載清單）</font></p>".format(pid))
            except Exception:
                pass
            return False
        if like_value < 300:
            self._record_like_less_pid(pid, like_value)
        if like_value < threshold:
            try:
                self._output.emit("<p><font color='black'>PID {} 愛心 {} 未達下載門檻 {}，下載階段跳過</font></p>".format(pid, like_value, threshold))
            except Exception:
                pass
            return False
        try:
            self._output.emit("<p><font color='gray'>PID {} 愛心 {} 已達門檻 {}</font></p>".format(pid, like_value, threshold))
        except Exception:
            pass
        return True

    def _is_no_space_error(self, err):
        try:
            if isinstance(err, OSError) and getattr(err, 'errno', None) == 28:
                return True
            txt = str(err).lower()
            return ('no space left on device' in txt) or ('errno 28' in txt)
        except Exception:
            return False

    def _free_disk_bytes(self):
        try:
            target = self.download_path if os.path.isdir(self.download_path) else (self.path or '.')
            return int(shutil.disk_usage(target).free)
        except Exception:
            return None

    def _trigger_no_space_stop(self, hint=''):
        try:
            if not self._no_space_alerted:
                self._output.emit("<p><font color='red'>磁碟空間不足，已停止下載。{}</font></p>".format(hint))
                self._no_space_alerted = True
        except Exception:
            pass
        self._stopped_by_request = True
        self._stop_after_group = True
        self._isPause = 2

    def _ensure_disk_space(self, expected_bytes=0, hint=''):
        free_bytes = self._free_disk_bytes()
        if free_bytes is None:
            return True
        required = max(0, int(expected_bytes)) + self._min_free_bytes
        if free_bytes < required:
            free_mb = round(free_bytes / 1024 / 1024, 1)
            need_mb = round(required / 1024 / 1024, 1)
            self._trigger_no_space_stop("可用 {} MB，至少需要約 {} MB。{}".format(free_mb, need_mb, hint))
            return False
        return True

    def _sleep_between_downloads(self, pid):
        # 不同 PID 之間的休眠
        if not self.single_mode_flag:
            return
        delay = self._calc_sleep_delay(self.download_wait_min, self.download_wait_max, pid=pid, no_cookie_min=self.download_wait_nocookie_min, no_cookie_max=self.download_wait_nocookie_max)
        cookie_used = self._is_cookie_used_for_pid(pid)
        ratio_text = 'cookie' if cookie_used else 'no-cookie'
        try:
            self._output.emit("<p><font color='green'>[下載階段][PID間] 等待 {} 秒 (PID {}, 模式 {}, cookie_used={})</font></p>".format(delay, pid, ratio_text, cookie_used))
        except Exception:
            pass
        for remaining in range(int(delay), 0, -1):
            if self._isPause == 2 or self._stop_after_group:
                break
            while self._isPause == 1:
                time.sleep(1)
            try:
                self._countdown.emit(remaining)
            except Exception:
                pass
            time.sleep(1)
        try:
            self._countdown.emit(0)
        except Exception:
            pass

    def _sleep_within_pid(self, pid):
        # 同一 PID 多張圖之間的短休眠
        if not self.single_mode_flag:
            return
        delay = self._calc_sleep_delay(self.intra_pid_wait_min, self.intra_pid_wait_max, pid=pid, no_cookie_min=self.intra_pid_wait_nocookie_min, no_cookie_max=self.intra_pid_wait_nocookie_max)
        cookie_used = self._is_cookie_used_for_pid(pid)
        ratio_text = 'cookie' if cookie_used else 'no-cookie'
        try:
            self._output.emit("<p><font color='gray'>[下載階段][同PID] 等待 {} 秒 (PID {}, 模式 {}, cookie_used={})</font></p>".format(delay, pid, ratio_text, cookie_used))
        except Exception:
            pass
        for remaining in range(int(delay), 0, -1):
            if self._isPause == 2:
                break
            while self._isPause == 1:
                time.sleep(1)
            try:
                self._countdown.emit(remaining)
            except Exception:
                pass
            time.sleep(1)
        try:
            self._countdown.emit(0)
        except Exception:
            pass

    def _is_cookie_used_for_pid(self, pid):
        """判斷此 PID 下載是否實際使用 cookie。"""
        try:
            # 簡化版：只看 all_url_meta.json 的 requires_cookie
            meta = self.url_meta.get(str(pid), {}) if isinstance(self.url_meta, dict) else {}
            req = meta.get('requires_cookie', None) if isinstance(meta, dict) else None
            if req is True:
                return bool(self.cookies and str(self.cookies).strip())
            if req is False:
                return False
            return False
        except Exception:
            return False
        return False

    def _calc_sleep_delay(self, min_sec, max_sec, pid=None, no_cookie_min=None, no_cookie_max=None):
        """計算休眠秒數：依 cookie 使用與否，使用不同等待區間。"""
        try:
            no_cookie_mode = (pid is not None) and (not self._is_cookie_used_for_pid(pid))
        except Exception:
            no_cookie_mode = False

        if no_cookie_mode:
            lo = int(no_cookie_min) if no_cookie_min is not None else max(0, int(round(int(min_sec))))
            hi = int(no_cookie_max) if no_cookie_max is not None else max(lo, int(round(int(max_sec))))
            if hi < lo:
                hi = lo
            return pyrandom.randint(lo, hi)
        return pyrandom.randint(int(min_sec), int(max_sec))

    def _extract_pid_from_download_url(self, url):
        try:
            pid_token = str(url).rsplit('/', 1)[1].split('_', 1)[0]
            m = re.match(r"^(\d+)", pid_token)
            return m.group(1) if m else pid_token
        except Exception:
            return None

    def _group_urls_by_pid(self, urls):
        groups = {}
        order = []
        for u in urls:
            pid = self._extract_pid_from_download_url(u)
            if not pid:
                continue
            if pid not in groups:
                groups[pid] = []
                order.append(pid)
            groups[pid].append(u)
        return order, groups

    def _download_pid_group(self, pid, urls):
        failed = []
        sess = requests.Session()
        has_actual_download = False  # 追踪是否有實際下載（非跳過）
        try:
            if not self._pass_like_threshold(pid):
                if str(pid) in self._permanent_skip_pids:
                    try:
                        with self._attempted_urls_lock:
                            for u in urls:
                                self._attempted_urls.add(u)
                    except Exception:
                        pass
                return failed, has_actual_download
            for idx, u in enumerate(urls):
                ret = self.gif_or_jpg(u, session=sess)
                if ret == -1:
                    # 跳過，不記錄為失敗，不觸發睡眠
                    pass
                elif ret != 0:
                    failed.append(ret)
                else:
                    # 成功下載
                    has_actual_download = True
                    # 只在成功下載後睡眠，且還有後續 URL
                    if idx < len(urls) - 1:
                        self._sleep_within_pid(pid)
                if self._isPause == 2:
                    break
        finally:
            try:
                sess.close()
            except Exception:
                pass
        return failed, has_actual_download

    def splitID(self,Filelist):
        exist_pid=[]
        print(len(Filelist))
        for file in  Filelist :
            if not re.search(r'\.jpg|\.png|\.gif', file):
                continue
            if not re.search(r'PID|illust', file):
                continue
            try:
                id=file.split('PID=')[1].split('_')[0]
                #print(id)
                if len(id)<12 and len(id)>4:
                    #print(id)
                    exist_pid.append(id)     
            except Exception as err:
                #print(err)
                try:
                    id=file.split('PID')[1].split(' ')[0]
                    if len(id)<=13 and len(id)>4:
                            #print(id)
                            exist_pid.append(id)
                    else:
                        if(str.isdigit(id.split('p')[0])):
                            try:
                                exist_pid.append(id.split('.')[0])
                                #print(id.split('.')[0])
                            except:
                                #print(id)
                                exist_pid.append(id)
                except Exception as err:
                    #print(err)
                    try:                    
                        id=file.split('_')[1]
                        #print(id)
                        if len(id)<12 and len(id)>4:
                            #print(id)
                            exist_pid.append(id)
                    except Exception as err:
                        #print(err)
                        try:      #illust_44773280_20220413_040534.jpg              
                            id=file.split('_')[1]
                            if len(id)<12 and len(id)>4:
                                #print(id)
                                exist_pid.append(id)
                        except Exception as err:
                            print(err)
                            pass
            #print(file)
        exist_pid=np.unique(exist_pid).tolist()
        return exist_pid 
    
    def get_filelist(self,path):
        Filelist = glob.glob(os.path.join(path, '*'))
        return Filelist
    
    def run(self):
        try:
            self._output.emit("<p><font color='red'>開始下載... </font></p>")   
            self._output.emit("<p><font color='red'>"+str(len(self.allurl))+"</font></p>")   
            if self.jxl_enable:
                try:
                    self._output.emit("<p><font color='blue'>JXL 自動轉檔已啟用：effort={}、刪原檔={}</font></p>".format(self.jxl_effort, self.jxl_delete_original))
                except Exception:
                    pass
            pid_order, pid_groups = self._group_urls_by_pid(self.allurl)
            self._output.emit("<p><font color='gray'>PID 分組完成：{} 個 PID、{} 個 URL</font></p>".format(len(pid_order), len(self.allurl)))

            failed_nested = []
            if self.single_mode_flag:
                self._output.emit("<p><font color='green'>下載模式：單執行緒 + 每個 PID 共用單一 Session</font></p>")
                self._output.emit("<p><font color='green'>同PID等待(cookie): {}~{} 秒；同PID等待(非cookie): {}~{} 秒；PID間等待(cookie): {}~{} 秒；PID間等待(非cookie): {}~{} 秒</font></p>".format(
                    self.intra_pid_wait_min, self.intra_pid_wait_max,
                    self.intra_pid_wait_nocookie_min, self.intra_pid_wait_nocookie_max,
                    self.download_wait_min, self.download_wait_max,
                    self.download_wait_nocookie_min, self.download_wait_nocookie_max))
                for idx, pid in enumerate(pid_order, start=1):
                    if self._stop_after_group:
                        try:
                            self._output.emit("<p><font color='orange'>收到中止要求：已在上一個 PID 組完成後停止</font></p>")
                        except Exception:
                            pass
                        break
                    if self._isPause == 2:
                        break
                    self._active_group_pid = pid
                    self._output.emit("<p><font color='black'>處理 PID {}/{}：{}（{} 張）</font></p>".format(idx, len(pid_order), pid, len(pid_groups.get(pid, []))))
                    failed_items, has_actual_download = self._download_pid_group(pid, pid_groups.get(pid, []))
                    failed_nested.append(failed_items)
                    self._active_group_pid = None
                    if self._isPause == 2:
                        break
                    if idx < len(pid_order) and has_actual_download:
                        self._sleep_between_downloads(pid)
            else:
                self._output.emit("<p><font color='gray'>下載模式：多執行緒（以 PID 為單位分派；每個 PID 仍共用單一 Session）</font></p>")
                max_workers = 4
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as self.executor:
                    futures = [self.executor.submit(self._download_pid_group, pid, pid_groups.get(pid, [])) for pid in pid_order]
                    for fu in concurrent.futures.as_completed(futures):
                        try:
                            failed_items, _ = fu.result()
                            failed_nested.append(failed_items)
                        except Exception:
                            failed_nested.append([])

            results = [i for item in failed_nested if isinstance(item, list) for i in item if i != 0]
            if results!=[]:   
                f = open((self.path+"/err_url"+".txt"), "w+")     #寫入文檔
                for text in results:
                    f.write(str(text[0])+' '+str(text[1])+'\n')
                f.close() 
            #print(results)     
            #self.gif_or_jpg(self.allurl[])
            stop_to_download = [self.q.get() for _ in range(self.q.qsize())]
            failed_to_download = []
            try:
                failed_to_download = [str(item[0]) for item in results if isinstance(item, (list, tuple)) and len(item) > 0 and str(item[0]).startswith('http')]
            except Exception:
                failed_to_download = []
            try:
                with self._attempted_urls_lock:
                    attempted_snapshot = set(self._attempted_urls)
            except Exception:
                attempted_snapshot = set()
            unattempted_urls = [u for u in self.allurl if u not in attempted_snapshot]
            remaining_urls = []
            seen = set()
            for u in stop_to_download + failed_to_download + unattempted_urls:
                if u in seen:
                    continue
                seen.add(u)
                remaining_urls.append(u)
            
            f = open((self.path+"/all_url"+".txt"), "w+")     #寫入文檔
            for text in remaining_urls:
                f.write(str(text)+'\n')
            f.close()
            try:
                self._output.emit("<p><font color='gray'>已更新 all_url.txt，剩餘 {} 筆待下載</font></p>".format(len(remaining_urls)))
            except Exception:
                pass
            # 重新載入 exist（json 優先）
            if os.path.isfile(self.exist_json_path):
                try:
                    with open(self.exist_json_path, 'r', encoding='utf-8') as file:
                        data = json.load(file)
                    self.exist_pid = set(str(x).replace('p0', '') for x in data) if isinstance(data, list) else set()
                except Exception:
                    self.exist_pid = set()
            elif os.path.isfile(self.legacy_exist_json_path):
                try:
                    with open(self.legacy_exist_json_path, 'r', encoding='utf-8') as file:
                        data = json.load(file)
                    self.exist_pid = set(str(x).replace('p0', '') for x in data) if isinstance(data, list) else set()
                except Exception:
                    self.exist_pid = set()
            else:
                try:
                    with open(self.exist_txt_path, encoding='utf-8') as file:
                        self.exist_pid = set(line.rstrip().replace('p0', '') for line in file if line.rstrip())
                except Exception:
                    self.exist_pid = set()
            download_id=self.splitID(self.get_filelist(self.download_path))
            for text in download_id:
                if text not in self.exist_pid:
                    self.exist_pid.add(text)
            # 主檔：exist_pid.json (需要備份)
            try:
                try:
                    from safe_io import atomic_write_json, atomic_write_text
                    atomic_write_json(self.exist_json_path, sorted(self.exist_pid), backup=True)
                    atomic_write_text(self.exist_txt_path, [str(pid) for pid in sorted(self.exist_pid)], backup=True)
                except Exception:
                    with open(self.exist_json_path, 'w', encoding='utf-8') as f:
                        json.dump(sorted(self.exist_pid), f, ensure_ascii=False, indent=2)
                    with open(self.exist_txt_path, 'w', encoding='utf-8') as f:
                        for pid in sorted(self.exist_pid):
                            f.write(str(pid) + '\n')
            except Exception:
                pass
            self._timechanged.emit(datetime.datetime.strftime(self.download_time,'%Y-%m-%d %H:%M:%S'))
            if self._stopped_by_request or (self._isPause==2):
                self._finished.emit('已終止')
                self._thenext.emit(-1)
            else:
                self._finished.emit('下載完成')
        except Exception as e:
            self._output.emit('獲得關注失敗 以下為報錯訊息')
            self._output.emit(output_err(e))
            self._thenext.emit(-1)
            

    def gif_or_jpg(self,url, session=None):
        while(self._isPause==1):
            time.sleep(1)
        try:
            with self._attempted_urls_lock:
                self._attempted_urls.add(url)
        except Exception:
            pass
        if self._isPause!=2:
            self.pid_now=self.pid_now+1
            self._signal.emit(1,self.pid_max)
        if self._isPause==2:
            self.q.put(url)
            return 0
        if not self._ensure_disk_space(0, hint='(下載前檢查)'):
            self.q.put(url)
            return [url, datetime.datetime.strftime(self.download_time, '%Y%m%d_%H%M%S')]
        if 'ugoira' in url:
            pid_token = url.rsplit('/',1)[1].rsplit('_',1)[0].rsplit('ugoira0')[0]
            m = re.match(r"^(\d+)", str(pid_token))
            pid = m.group(1) if m else str(pid_token)
            if(pid in self.exist_pid):
                print('跳過')
                return -1  # 跳過標記
            else:
                return self.gif_download(url, session=session)
        else:
            pid_token = str(url.rsplit('/',1)[1].split('_',1)[0])
            m = re.match(r"^(\d+)", pid_token)
            pid = m.group(1) if m else pid_token
            if(pid in self.exist_pid):
                print('跳過')
                return -1  # 跳過標記
            else:
                return self.jpg_download(url, session=session)   
    def __del__(self):
        try:
            executor = getattr(self, 'executor', None)
            if executor is not None:
                executor.shutdown(wait=False)
        except Exception:
            pass
        self.wait()

    def _build_pixiv_image_headers(self, pid, need_cookie=False):
        headers = {
            'User-Agent': self.agent,
            'Referer': 'https://www.pixiv.net/artworks/{}'.format(pid),
            'Origin': 'https://www.pixiv.net'
        }
        if need_cookie is True and self.cookies:
            headers['Cookie'] = self.cookies
        return headers

    def _get_with_retry(self, http, url, headers, stream=True, timeout=20, retries=3):
        last_err = None
        for attempt in range(retries):
            try:
                res = http.get(url, headers=headers, verify=False, stream=stream, timeout=timeout)
                if res.status_code >= 500 and attempt < retries - 1:
                    time.sleep(min(1.5 * (attempt + 1), 4))
                    continue
                return res
            except Exception as err:
                last_err = err
                if attempt < retries - 1:
                    time.sleep(min(1.5 * (attempt + 1), 4))
                    continue
        if last_err:
            raise last_err
        raise RuntimeError('request failed without exception')

    def gif_download(self,url, session=None):
        my_time = self.download_time
        try:
            pid_candidate = url.rsplit('/',1)[1].rsplit('_')[0]
            m = re.match(r"^(\d+)", pid_candidate)
            pid = m.group(1) if m else pid_candidate
            need_cookie = None
            try:
                meta = self.url_meta.get(str(pid), {}) if isinstance(self.url_meta, dict) else {}
                if isinstance(meta, dict) and meta:
                    need_cookie = meta.get('requires_cookie', None)
                if need_cookie is None:
                    need_cookie = pixiv_api.get_pixiv_cookie_requirement(pid)
            except Exception:
                need_cookie = None
            info = pixiv_api.Pixiv_info('https://www.pixiv.net/artworks/'+pid, self.agent, cookie=self.cookies)
            normalized = self._normalize_pixiv_info(info)
            if not normalized:
                try:
                    self._output.emit("<p><font color='orange'>PID {} 作品資訊不足，略過（等待下次重試）</font></p>".format(pid))
                except Exception:
                    pass
                return [url, my_time.strftime('%Y%m%d_%H%M%S')]
            tag,like,pagecount,img_url = normalized
            url='https://www.pixiv.net/ajax/illust/%s/ugoira_meta?lang=zh_tw'%pid
            headers = self._build_pixiv_image_headers(pid, need_cookie=bool(need_cookie is True and self.cookies))
            try:
                self._pid_cookie_used[str(pid)] = bool(need_cookie is True and self.cookies)
            except Exception:
                pass
            http = session if session is not None else requests
            htmlfile = self._get_with_retry(http, url, headers=headers, stream=True, timeout=20, retries=3)
            if htmlfile.status_code != 200:
                print(f"[pixiv_thread] PID {pid} 取得 ugoira_meta 失敗，狀態碼：{htmlfile.status_code}")
                print(f"[pixiv_thread] 回應內容：{htmlfile.text[:500]}")
                return None
            htmlfile.raise_for_status()
            try:
                gif_info=json.loads((htmlfile.content))['body']
                download_url=gif_info['originalSrc']
                delay_info=[item["delay"] for item in gif_info["frames"]]
                delay=sum(delay_info)/len(delay_info)
                url=download_url
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"[pixiv_thread] PID {pid} JSON 解析失敗：{e}")
                print(f"[pixiv_thread] 回應內容：{htmlfile.text[:500]}")
                return None
            headers = self._build_pixiv_image_headers(pid, need_cookie=bool(need_cookie is True and self.cookies))
            htmlfile = self._get_with_retry(http, url, headers=headers, stream=True, timeout=30, retries=3)
            size = 0
            chunk_size = 1024
            my_time=self.download_time
            if htmlfile.status_code == 200: #判斷是否回應成功
                    #print('Start download,[File size]:{size:.2f} MB'.format(size = content_size / chunk_size /1024)) #開始下載，顯示下載檔案大小
                    self.timelock.lock()
                    
                    self.download_time= self.download_time+datetime.timedelta(seconds=1)
                    print(datetime.datetime.strftime(self.download_time,'%Y-%m-%d %H:%M:%S'))
                    self.timelock.unlock()
                    rename=('illust_'+pid+my_time.strftime('_%Y%m%d_%H%M%S.zip'))
                    
                    filepath = self.download_path+rename #設置圖片名稱，注：必須加上副檔名
                    print(filepath)
                    content_len = 0
                    try:
                        content_len = int(htmlfile.headers.get('Content-Length', 0) or 0)
                    except Exception:
                        content_len = 0
                    if not self._ensure_disk_space(content_len, hint='(PID {} ugoira zip)'.format(pid)):
                        return [url,my_time.strftime('%Y%m%d_%H%M%S')]
                    with open(filepath,'wb') as file: #顯示進度條
                        for data in htmlfile.iter_content(chunk_size = chunk_size):
                            try:
                                file.write(data)
                            except OSError as ose:
                                if self._is_no_space_error(ose):
                                    self._trigger_no_space_stop('(PID {} ugoira 寫檔中)'.format(pid))
                                    return [url,my_time.strftime('%Y%m%d_%H%M%S')]
                                raise
                            size +=len(data)
                            # print('\r'+'[%s]:%s%.2f%%'% (rename,'█'*int(size*50/ content_size), float(size / content_size * 100)) ,end=' ')  
            temp_file_list = []
            file_path = self.download_path+'/'+pid
            try:
                os.mkdir(file_path)
            except Exception as err:
                print(err)
                pass
            zipo = zipfile.ZipFile(filepath,"r")
            for file in zipo.namelist():
                temp_file_list.append(os.path.join(file_path,file))
                zipo.extract(file, file_path)
            zipo.close()
            os.remove(filepath)
            image_data=[]
            for file in temp_file_list:
                image_data.append(imageio.imread(file))
            hashtag=" "    
            name=""
            try:
                for many in tag:
                    if len(hashtag)>230: 
                        break
                    else: 
                        hashtag = hashtag+' '+many
                if (self.notime==False):
                    name=name+my_time.strftime('%Y%m%d_%H%M%S')
                if(self.notag==True):
                    if name=='':
                        name='PID'+pid+'.gif' 
                    
                    else:
                        name=name+'_'+'PID'+pid+'.gif' 
                else:
                    if name=='':
                        name='PID'+pid+hashtag+'.gif' 
                    else:    
                        name=name+'_'+'PID'+pid+hashtag+'.gif' 
            except:
                name='illust_'+pid+(my_time.strftime('_%Y%m%d_%H%M%S.gif'))
            saved_path = None
            if (self.create_dir==False):
                if (self.no_R18G_dir==True):
                    saved_path = (self.download_path+name)
                    imageio.mimsave(saved_path,image_data,"GIF", duration=delay / 1000)
                else:
                    if 'R-18G' in tag:
                        if not os.path.exists(self.download_path+'R-18G/'):
                            os.mkdir(self.download_path+'/R-18G/')
                        saved_path = (self.download_path+'/R-18G/'+name)
                        imageio.mimsave(saved_path,image_data,"GIF", duration=delay / 1000)
                    else:
                        saved_path = (self.download_path+name)
                        imageio.mimsave(saved_path,image_data,"GIF", duration=delay / 1000)    
            else:
                userId=pixiv_api.userId('https://www.pixiv.net/artworks/'+pid,self.agent)
                if not os.path.exists(self.download_path+str(userId)):
                    os.mkdir(self.download_path+str(userId))
                if ((self.no_R18G_dir==True) or ('R-18G' not in tag)):
                    saved_path = (self.download_path+'/'+str(userId)+'/'+name)
                    imageio.mimsave(saved_path,image_data,"GIF", duration=delay / 1000)
                else:
                    if not os.path.exists(self.download_path+str(userId)+'/R-18G/'):
                        os.mkdir(self.download_path+'/'+str(userId)+'/R-18G/')
                    saved_path = (self.download_path+'/'+str(userId)+'/R-18G/'+name)
                    imageio.mimsave(saved_path,image_data,"GIF", duration=delay / 1000)
            if saved_path:
                self._convert_file_to_jxl_lossless(saved_path)
            for file in temp_file_list:
                os.remove(file)
            try:
                shutil.rmtree(file_path)
            except OSError as e:
                print(e)
            except Exception as e:
                print(e)
            else:
                print("The directory is deleted successfully")
            return 0
        except Exception as err:
            if self._is_no_space_error(err):
                self._trigger_no_space_stop('(ugoira 下載/轉檔)')
            print(err,self.cookies)
        return [url,my_time.strftime('%Y%m%d_%H%M%S')]          
    
    def jpg_download(self,url, session=None): 
        self.timelock.lock()
        timetag=self.download_time.strftime('%Y%m%d_%H%M%S')
        self.download_time += datetime.timedelta(seconds=1)
        self.timelock.unlock()
        for i in range (0,5): #重試5次 如果下載成功 將會直接Return回去
            try:
                pid_candidate = str(url).rsplit('/',1)[1].rsplit('_',1)[0]  #圖片id候選
                m = re.match(r"^(\d+)", pid_candidate)
                pid = m.group(1) if m else pid_candidate
                need_cookie = None
                meta = self.url_meta.get(str(pid), {}) if isinstance(self.url_meta, dict) else {}
                if isinstance(meta, dict) and meta:
                    tag = meta.get('tag', [])
                    like = meta.get('like', 0)
                    pagecount = meta.get('pagecount', 1)
                    img_url = meta.get('img_url', None)
                    need_cookie = meta.get('requires_cookie', None)
                else:
                    try:
                        need_cookie = pixiv_api.get_pixiv_cookie_requirement(pid)
                    except Exception:
                        need_cookie = None

                    if need_cookie is True:
                        info = pixiv_api.Pixiv_info('https://www.pixiv.net/artworks/'+pid,self.agent,cookie=self.cookies)
                    elif need_cookie is False:
                        info = pixiv_api.Pixiv_info('https://www.pixiv.net/artworks/'+pid,self.agent)
                    else:
                        # 未知時交給 Pixiv_info 自動判斷（先不帶 cookie，失敗再帶）
                        info = pixiv_api.Pixiv_info('https://www.pixiv.net/artworks/'+pid,self.agent,cookie=self.cookies)
                        try:
                            need_cookie = pixiv_api.get_pixiv_cookie_requirement(pid)
                        except Exception:
                            need_cookie = None
                    normalized = self._normalize_pixiv_info(info)
                    if not normalized:
                        raise ValueError("Pixiv_info 回傳格式異常")
                    tag,like,pagecount,img_url = normalized

                if(like==404 and tag ==404):
                    return
                p=str(url).rsplit('_',1)[1].rsplit('.',1)[0]    #第幾張圖片
                picture_format = url.rsplit('.',1)[1]
                headers = self._build_pixiv_image_headers(pid, need_cookie=bool(need_cookie is True and self.cookies))
                try:
                    self._pid_cookie_used[str(pid)] = bool(need_cookie is True and self.cookies)
                except Exception:
                    pass
                http = session if session is not None else requests
                htmlfile = self._get_with_retry(http, url, headers=headers, stream=True, timeout=20, retries=3)
                htmlfile.raise_for_status() 
                size = 0
                chunk_size = 1024
                hashtag=''
                name=''
                if self.notime==False:
                    name=name+timetag
                if htmlfile.status_code == 200: #判斷是否回應成功
                    
                    try:
                        for many in tag:
                            if len(hashtag)>230: 
                                break
                            else: 
                                hashtag=hashtag+' '+many
                        if self.notag==False:
                            if (name==''):
                                name='PID'+pid+p+hashtag+'.'+picture_format 
                            else:     
                                name=name+'_'+'PID'+pid+p+hashtag+'.'+picture_format 
                        else:
                            if (name==''):
                                name='PID'+pid+p+'.'+picture_format 
                            else:    
                                name=name+'_'+'PID'+pid+p+'.'+picture_format 
                    except:
                        name=('illust_'+pid+p+timetag+'.'+picture_format)
                    tag=str(tag)
                    filepath=''
                    if self.create_dir==False:          #不為每個畫師創資料夾
                    #if 'R-18G' in tag or '糞'in tag or '子宮脫' in tag :
                        if self.no_R18G_dir==False:#為R-18G創一個資料夾
                            if 'R-18G' in tag:
                                if not os.path.exists(self.download_path+'/R-18G/'):
                                    #print('mkdir ' + path)
                                    os.mkdir(self.download_path+'/R-18G/')    
                                filepath = self.download_path+'/R-18G/'+name #設置圖片名稱，注：必須加上副檔名
                            else:
                                filepath = self.download_path+name #設置圖片名稱，注：必須加上副檔名

                        else:
                            filepath = self.download_path+name #設置圖片名稱，注：必須加上副檔名
                         
                    else:                               #為每個畫師創資料夾
                        userId=pixiv_api.userId('https://www.pixiv.net/artworks/'+pid,self.agent)
                        if not os.path.exists(self.download_path+str(userId)):
                            os.mkdir(self.download_path+str(userId))
                        if self.no_R18G_dir==False:#為R-18G創一個資料夾
                            if 'R-18G' in tag:
                                if not os.path.exists(self.download_path+'/'+str(userId)+'/R-18G/'):
                                    #print('mkdir ' + path)
                                    os.mkdir(self.download_path+'/'+str(userId)+'/R-18G/')    
                                filepath = self.download_path+'/'+str(userId)+'/R-18G/'+name #設置圖片名稱，注：必須加上副檔名
                            else:
                                filepath = self.download_path+'/'+str(userId)+name #設置圖片名稱，注：必須加上副檔名
                        else:
                            filepath = self.download_path+'/'+str(userId)+name #設置圖片名稱，注：必須加上副檔名
                    with open(filepath,'wb') as file: #顯示進度條
                        content_len = 0
                        try:
                            content_len = int(htmlfile.headers.get('Content-Length', 0) or 0)
                        except Exception:
                            content_len = 0
                        if not self._ensure_disk_space(content_len, hint='(PID {} 圖片)'.format(pid)):
                            return [url,timetag]
                        for data in htmlfile.iter_content(chunk_size = chunk_size):
                            try:
                                file.write(data)
                            except OSError as ose:
                                if self._is_no_space_error(ose):
                                    self._trigger_no_space_stop('(PID {} 圖片寫檔中)'.format(pid))
                                    return [url,timetag]
                                raise
                            size +=len(data)     
                if filepath:
                    self._convert_file_to_jxl_lossless(filepath)
                return 0
            except Exception as err: 
                if self._is_no_space_error(err):
                    self._trigger_no_space_stop('(jpg 下載/寫檔)')
                    return [url,timetag]
                print(err)
                # 只有最後一次失敗才回報，前面失敗交給重試流程
                if i >= 4:
                    return [url,timetag]
                time.sleep(min(1.5 * (i + 1), 4))
                continue
    
    def pause(self):
        self._output.emit("<p><font color='red'>暫停</font></p>")
        self._isPause = 1

    def resume(self):
        self._output.emit("<p><font color='red'>繼續</font></p>")
        self._isPause = 0
        
    def stop(self):
        if self.single_mode_flag:
            self._stop_after_group = True
            self._stopped_by_request = True
            # 防呆：若目前在暫停狀態，先解除暫停，避免永遠卡在 while self._isPause == 1
            if self._isPause == 1:
                self._isPause = 0
                try:
                    self._output.emit("<p><font color='orange'>偵測到暫停中，已自動解除暫停以執行中止流程</font></p>")
                except Exception:
                    pass
            try:
                if self._active_group_pid is not None:
                    self._output.emit("<p><font color='orange'>已收到中止要求，將於當前 PID {} 組下載完成後停止</font></p>".format(self._active_group_pid))
                else:
                    self._output.emit("<p><font color='orange'>已收到中止要求，當前無活動 PID 組，立即停止</font></p>")
                    self._isPause = 2
            except Exception:
                pass
            return
        self._stopped_by_request = True
        if self._isPause == 1:
            self._isPause = 0
        self._output.emit("<p><font color='red'>中止</font></p>")
        self._isPause = 2 



class test_thread(QThread):
    valueChange = pyqtSignal(int,int)

    def __init__(self, *args, **kwargs):
        super(test_thread, self).__init__(*args, **kwargs)
        self._isPause = False
        self._value = 0
        self.cond = QWaitCondition()

    def pause(self):
        #print(1)
        self._isPause = True

    def resume(self):
        #print(12)
        self._isPause = False
        

    def run(self):
        while 1:
            print(1)
            while self._isPause:
                print(10)
                time.sleep(1)
            time.sleep(1)



