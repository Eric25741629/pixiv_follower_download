from PyQt5.QtCore import *
import time
import os
import concurrent.futures
import requests
import numpy as np
import threading
from functools import partial
from pixiv_api import *
from app.core.pixiv_thread_utils import (
    atomic_write_json,
    atomic_write_text,
    output_err,
)
from app.core.pixiv_thread_base import (
    PauseableThread,
)

class get_following(PauseableThread):
    '''抓取使用者關注的畫師清單'''
    _signal = pyqtSignal(int,int)
    _output=pyqtSignal(str)
    _finished = pyqtSignal(str)
    _thenext = pyqtSignal(int)
    def __init__(self,userid,cookies,Agent,hide_mode):
        super().__init__()
        self.userid=userid
        self.cookies=cookies
        self.Agent=Agent
        self.path=os.getenv('APPDATA')+r'/pixiv_download/'
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
        while self._isPause == 1:
            time.sleep(1)
        if self._isPause == 2:
            return []
        global pid_num
        pid_num=pid_num+100
        url = ('https://www.pixiv.net/ajax/user/{}/following?offset='+str(times)+'&limit=100&rest='+state+'&tag=&lang=zh_tw')
        res = requests.get(url.format(id), headers=headers, timeout=(10, 30))
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
    def illusts(self):              # 以使用者 ID 取得全部關注畫師
        headers = {
            'User-Agent': self.Agent,
            'Cookie':self.cookies
            ,'referer': 'https://www.pixiv.net/users/'+str(self.userid)+'/following',        
        }
        times=0
        url = ('https://www.pixiv.net/ajax/user/{}/following?offset='+str(times)+'&limit=1&rest=show&tag=&lang=zh_tw') # 先查公開關注總數
        print(url.format(self.userid))

        res = requests.get(url.format(self.userid), headers=headers, timeout=(10, 30))
        print(res.text)
        show_total_num=(res.json()['body']['total'])
        show_list = list(range(0, show_total_num+200, 100))

        if (self.hide==False):
            #print("yes")
            url = ('https://www.pixiv.net/ajax/user/{}/following?offset='+str(times)+'&limit=1&rest=hide&tag=&lang=zh_tw')
            res = requests.get(url.format(self.userid), headers=headers, timeout=(10, 30))
            hide_total_num=(res.json()['body']['total'])
            hide_list=[i for i in range(0,hide_total_num+200,100)]
            self.max=int(hide_total_num+show_total_num)
        else:
            self.max=int(show_total_num)
        self._output.emit(f'total following: {self.max}')
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
            self._output.emit('抓取 following 完成')
            atomic_write_text(os.path.join(self.path, "following.txt"), texts, backup=True)
            atomic_write_json(os.path.join(self.path, "following.json"), texts, backup=True)
            self._output.emit("<p><font color='red'>抓取關注畫師完成</font></p>")
            self._thenext.emit(2)
            self._finished.emit('抓取關注畫師完成')
        except Exception as e:
            self._output.emit('Task failed')
            self._output.emit(output_err(e))
            self._thenext.emit(-1)

