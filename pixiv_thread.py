from PyQt5 import QtWidgets, QtCore
import sys
from PyQt5.QtCore import *
import time
import json
import multiprocessing as MP
import os
import random
import threading
import time
from functools import partial
from multiprocessing import Pool
from queue import Queue
import concurrent.futures
import time
import tqdm as tqdm
from tqdm import trange
import requests
global pid_num
pid_num=0
global pid_len
pid_len=0
class get_pixiv_author_imgID_Thread(QThread):
    _signal = pyqtSignal(int,int)

    def __init__(self,Author_list,Agent,path,cookies,exist_pid):
        super(get_pixiv_author_imgID_Thread,self).__init__()
        self.Author_list=Author_list
        self.Agent=Agent
        self.path=path
        self.cookies=cookies
        self.exist_pid=exist_pid
    def __del__(self):
        self.wait()
    def run(self):
        global pid_len
        #print(len(self.Author_list))
        pid_len=len(self.Author_list)
        #Author_list = np.array_split(self.Author_list,700)   #將獲取的文檔分成100等分
        #print(len(self.Author_list))
        func=partial(self.thread_no_use_seleium_get_pid,self.cookies[0],self.Agent,self.path,'1')
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:  
            results = list(tqdm(executor.map(func, self.Author_list), total=len(self.Author_list))) 
        results=([i for item in results for i in item]) 
        print(len(results))
        end=[]
        for i in trange(0,len(results)):
            if(results[i] not in self.exist_pid):
                #print(results[i])
                end.append(results[i])
        print(len(end))
        f = open((self.path+"/pictures_id.txt"), "w+")     #讀取寫入的文檔
        for text in end:
            f.write(str(text)+'\n')
        f.close()
    def thread_no_use_seleium_get_pid(self,cookie,Agent,path,num,author_pids):
        global pid_num
        global pid_len
        pid_num=pid_num+1
        self._signal.emit(int(pid_num),pid_len)
        pid=[]
        try:
            url='https://www.pixiv.net/ajax/user/'+author_pids+'/profile/all?lang=zh%27'
            headers = {
            'User-Agent': Agent,
            'Cookie':cookie
            ,'referer': 'https://www.pixiv.net/users/'+author_pids,        
            }
            res = requests.get(url, headers=headers)
            resdicts = res.json()['body']['illusts']
            #print(resdicts)
            for key in resdicts:
                pid.append(key)
        except Exception as err:
            print(err)
            f = open((path+"authorPids_err"+str(num)+".txt"), "a+")
            f.write(author_pids+'\n')
            f.close() 
        return pid
class get_imgurl_thread(QThread):
    
    def run(self,cookie,Agent):
        path=os.getenv('APPDATA')+r'/pixiv_download/'
        #cookie='p_ab_id=5; p_ab_id_2=5; p_ab_d_id=1125130963; first_visit_datetime_pc=2021-02-23+02%3A32%3A11; yuid_b=OJRESQg; a_type=0; b_type=1; login_ever=yes; privacy_policy_notification=0; c_type=34; PHPSESSID=27915696_60l9nve4HS7Z2Y0bngGXRZQwV4W4DvJ0; privacy_policy_agreement=3; QSI_S_ZN_5hF4My7Ad6VNNAi=v:0:0; tag_view_ranking=0xsDLqCEW6~qWFESUmfEs~LVSDGaCAdn~QKeXYK2oSR~Txs9grkeRc~RTJMXD26Ak~kGYw4gQ11Z~lH5YZxnbfC~Lt-oEicbBr~_EOd7bsGyl~yS_WrRrWFi~G-44hwuIPi~LLyDB5xskQ~Ie2c51_4Sp~HLWLeyYOUF~DADQycFGB0~sqGkVxMuMR~jk9IzfjZ6n~uvBGOtCzqF~MM6RXH_rlN~aKhT3n4RHZ~HY55MqmzzQ~Ti1gvrVQFO~bXMh6mBhl8~RokSaRBUGr~aC55Umcfh1~zsm1ECW5Wb~5f1R8PG9ra~xa5-CDAPro~G_f4j5NH8i~v3nOtgG77A~0RGtdYkK6L~abNIEh2zTB~Bd2L9ZBE8q~0jyux9PxkH~QaiOjmwQnI~n39RQWfHku~vxqZQOR3t2~hk_QPyZfi8~Tg1PbOMGRv~qXzcci65nj~ZTBAtZUDtQ~1VgdMhBiax~dUhrZMpRPB~tgP8r-gOe_~YTKjYV1RQx~Je_lQPk0GY~m3EJRa33xU~iVTmZJMGJj~rMC0CLW0cf~mHukPa9Swj~GuK7T6aGv6~T6NhuB95ST~CLTDpOEHJL~gpglyfLkWs~NGpDowiVmM~MnGbHeuS94~mZurA-1CO-~Am8pyjYCcZ~Riqeg_qBGT~jfnUZgnpFl~BtXd1-LPRH~ujS7cIBGO-~zZZn32I7eS~CrFcrMFJzz~ZN5DR5ie1W~AZ1ov2QNRs~N7rBHi7ijr~QzKFCsGzn-~PBxKNk7VAD~zyKU3Q5L4C~vAwbTkrP0I~P5-w_IbJrm~Ltbk6w58aR~l2rugVKl6u~ajFGI2BXvo~R0DtApn-IB~W4_X_Af3yY~OUF2gvwPef~D4hLr_YmAD~QIa7PLv7ZL~EQ_o6ZyXFg~lf-Uj4GKzU~2FO_ideA5k~18j5-cWRq2~FPCeANM2Bm~TWrozby2UO~9Gbahmahac~2QTW_H5tVX~bplY14maDo~jjVAJCBCtW~B2kc8vAuXw~m3sqCXWo7m~k39B1CkQWC~muA8Dd9eL4~I-ST5EF_lI~wbvCWCYbkM~mVhi1hBMit~Hry6GxyqEm~i8u6Dgt7ao; __cf_bm=cqoyzD4i.qO0s1sUnjhOf9p5ytamrWA2qApQNhhiIKE-1656319872-0-AaXwpJas6wECDAH0caPNgFN5+Y5wjvrFlFzdxBuyzQz6oQGTN8qILCJhy4DeWPqBE9H8Msy1ymtWXbBqLJ6dRm160hdvQQHr56qP0p3ZdhTI'
        #Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36 Edg/96.0.1054.62'
        #print(pixiv_api.get_download_url(path,cookie,Agent,'1','99177788'))
        with open((path+r"/pictures_id.txt")) as file:     #讀取寫入的文檔
            pictures_id = [line.rstrip() for line in file]
        func=partial(pixiv_api.get_download_url,path,cookie,Agent,1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:  
            results = list(tqdm.tqdm(executor.map(func, pictures_id), total=len(pictures_id)))
        results=([i for item in results for i in item]) 
        pid=[i for i in results if 'https' not in i]
        results=[i for i in results if 'https' in i]
        f = open((path+"/innvid"+".txt"), "w+")     #讀取寫入的文檔
        for text in results:
            f.write(str(text)+'\n')
        f.close()
        results = np.array_split(results,100)  
        for i in range(0,100):   
            f = open((path+"/pictures_url"+str(i)+".txt"), "w+")     #讀取寫入的文檔
            for text in results[i]:
                f.write(str(text)+'\n')
            f.close()
