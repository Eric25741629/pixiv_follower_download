import time
import os
import concurrent.futures
import requests
import numpy as np
import threading
from functools import partial
from pixiv_api import *
from app.core.worker_event import WorkerEvent
from app import i18n
from app.core.pixiv_thread_utils import (
    atomic_write_json,
    atomic_write_text,
    output_err,
    safe_json,
)
from app.core.pixiv_thread_base import (
    PauseableThread,
)

class get_following(PauseableThread):
    '''抓取使用者關注的畫師清單'''
    def __init__(self, q, userid, cookies, Agent, following_scope):
        super().__init__(q)
        self.userid=userid
        self.cookies=cookies
        self.Agent=Agent
        self.path=os.getenv('APPDATA')+r'/pixiv_download/'
        self._partial_following = []
        self._partial_lock = threading.Lock()
        self.following_scope = self._coerce_following_scope(following_scope)
        # Legacy compatibility: old code used hide=True to mean "public only".
        self.hide = self.following_scope == "public"
        self.max=0

    @staticmethod
    def _coerce_following_scope(value):
        if hasattr(value, "isChecked"):
            try:
                return "public" if bool(value.isChecked()) else "all"
            except Exception:
                return "all"
        if isinstance(value, bool):
            return "public" if value else "all"
        scope = str(value or "all").strip().lower()
        if scope == "show":
            return "public"
        if scope == "hide":
            return "private"
        return scope if scope in {"public", "private", "all"} else "all"

    @staticmethod
    def _following_rest_values(scope):
        if scope == "public":
            return ["show"]
        if scope == "private":
            return ["hide"]
        return ["show", "hide"]

    def _flush_following_snapshot(self):
        try:
            with self._partial_lock:
                texts = np.unique(self._partial_following).tolist() if self._partial_following else []
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

    def _on_pause_hook(self):
        self._flush_following_snapshot()

    def _on_stop_hook(self):
        self._flush_following_snapshot()

    def get_follow_illust(self,id,headers,state,times):
        '''取得指定分頁的關注畫師（公開或私人）'''
        self._pause_event.wait()
        if self._stop_event.is_set():
            return []
        url = ('https://www.pixiv.net/ajax/user/{}/following?offset='+str(times)+'&limit=100&rest='+state+'&tag=&lang=zh_tw')
        resdicts = []
        for attempt in range(3):
            try:
                res = requests.get(url.format(id), headers=headers, timeout=(10, 30))
                res.raise_for_status()
                resdicts = safe_json(res, 'body', 'users', default=[])
                break
            except Exception as e:
                if attempt < 2:
                    print(output_err(e))
                    time.sleep(2)
                else:
                    resdicts = []
        self._q.put(WorkerEvent("progress", (100, self.max)))
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
    def illusts(self):              # 以使用者 ID 取得全部關注畫師
        headers = {
            'User-Agent': self.Agent,
            'Cookie':self.cookies
            ,'referer': 'https://www.pixiv.net/users/'+str(self.userid)+'/following',        
        }
        times=0
        rest_values = self._following_rest_values(self.following_scope)
        page_ranges = {}
        total_by_rest = {}
        for rest in rest_values:
            url = (
                'https://www.pixiv.net/ajax/user/{}/following?offset='
                + str(times)
                + '&limit=1&rest='
                + rest
                + '&tag=&lang=zh_tw'
            )
            print(url.format(self.userid))
            total_num = 0
            for attempt in range(3):
                try:
                    res = requests.get(url.format(self.userid), headers=headers, timeout=(10, 30))
                    res.raise_for_status()
                    if rest == "show":
                        print(res.text)
                    total_num = safe_json(res, 'body', 'total', default=0)
                    break
                except Exception as e:
                    if attempt < 2:
                        print(output_err(e))
                        time.sleep(2)
                    else:
                        total_num = 0
            total_by_rest[rest] = total_num
            page_ranges[rest] = list(range(0, total_num+200, 100))
        self.max = int(sum(total_by_rest.values()))
        self._q.put(WorkerEvent("output", i18n.t("log.following.start", n=self.max)))
        results = []
        for rest in rest_values:
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as self.executor:
                func=partial(self.get_follow_illust,self.userid,headers,rest)
                pixiv_following = list(self.executor.map(func,page_ranges[rest]))
                results.extend([i for item in pixiv_following for i in item])
        return results
    def run(self):
        try:
            all_pixiv_ids = self.illusts()
            texts = np.unique(all_pixiv_ids).tolist()
            atomic_write_text(os.path.join(self.path, "following.txt"), texts, backup=True)
            atomic_write_json(os.path.join(self.path, "following.json"), texts, backup=True)
            self._q.put(WorkerEvent("output",
                f"<p><font color='green'>{i18n.t('log.following.done', n=len(texts))}</font></p>"))
            # Emit finished BEFORE next so the dispatcher tears down step 1 first
            # and THEN starts step 2 — otherwise handle_finished re-marks the
            # just-started step 2 'done' and disables its pause/stop. (B7)
            self._q.put(WorkerEvent("finished", i18n.t("log.following.done", n=len(texts))))
            self._q.put(WorkerEvent("next", 2))
        except Exception as e:
            self._q.put(WorkerEvent("output",
                f"<p><font color='red'>{i18n.t('log.following.fail')}</font></p>"))
            self._q.put(WorkerEvent("output", output_err(e)))
            self._q.put(WorkerEvent("next", -1))

