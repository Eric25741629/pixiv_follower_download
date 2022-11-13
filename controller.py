from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtWidgets import QFileDialog
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from tqdm import trange
from ui import Ui_MainWindow
import pixiv_api
from queue import Queue
import numpy as np
import time,random
import threading
import os
from multiprocessing import Pool
import requests
from tqdm import tqdm, trange
from multiprocessing import Pool
import multiprocessing as MP
from queue import Queue
import os
from functools import partial
import json
import tqdm
import download_url
global cookies
global Agent
global path
import concurrent.futures
import download_img
class get_pixiv_author_imgID_Thread(QThread):
        def __init__(self):
            super(get_pixiv_author_imgID_Thread,self).__init__()
        def run(self):
            print(len(self.Author_list))
            #Author_list = np.array_split(self.Author_list,700)   #將獲取的文檔分成100等分
            #print(len(self.Author_list))
            func=partial(pixiv_api.thread_no_use_seleium_get_pid,self.cookies[0],self.Agent,self.path,'1')
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:  
                results = list(tqdm.tqdm(executor.map(func, self.Author_list), total=len(self.Author_list))) 
            
            results=([i for item in results for i in item]) 
            print(len(results))
            end=[]
            for i in trange(0,len(results)):
                if(results[i] not in self.exist_pid):
                    #print(results[i])
                    end.append(results[i])
            
            '''p=Pool(processes= 16)
            
            for result in tqdm.tqdm(p.imap_unordered(MainWindow_controller.in_the_pid, results), total=len(results)):
                if(result!=0):
                    end.append(result)
            p.close()
            p.join()'''
            #results=[x for x in results if not MainWindow_controller.in_the_pid(x)]
            print(len(end))
            f = open((self.path+"/pictures_id.txt"), "w+")     #讀取寫入的文檔
            for text in end:
                f.write(str(text)+'\n')
            f.close()
class MainWindow_controller(QtWidgets.QMainWindow):
    path='none'
    exist_pid=''
    Author_list=[]
    cookies=[]
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36 Edg/96.0.1054.62'
    picture_ids=Queue()
    path='unknown'
    i=-1
    exist_pid='unknown'
    download_path=None
    start=0
    stop=100
    def __init__(self):
        super().__init__() # in python3, super(Class, self).xxx = super().xxx
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.setup_control()
    def setup_control(self):
        # TODO
        #self.ui.actionfile.triggered.connect(self.open_folder) 
        #self.path=self.open_folder()
        self.user_data()
        self.set_cookies()
        self.ui.getpixiv_author.clicked.connect(lambda:self.get_pixiv_author(self.path))
        self.ui.getpixiv_author_imgID.clicked.connect(lambda:get_pixiv_author_imgID_Thread.run(self))
        self.ui.get_url.clicked.connect(lambda:download_url.main(self.cookies,self.Agent))
        self.ui.download_img.clicked.connect(lambda:download_img.download_img_main(self.download_path,self.start,self.stop,self.cookies,self.Agent))
                                                                         
        #self.ui.getpixiv_author.clicked.connect(lambda:self.get_pixiv_author(self.path))
        #self.ui.label.setText('Happy World!')
        
    def user_data(self):
        def load_data(self):
            self.path=os.getenv('APPDATA')+r'/pixiv_download/'
            if not os.path.exists(self.path):
                os.mkdir(self.path)
            if not os.path.exists(self.path+r"/existPID.txt"):
                print("找不到existPID文件")
            else:
                with open((self.path+r"/existPID.txt")) as file:     #讀取寫入的文檔
                    self.exist_pid = [line.rstrip().replace("p0","") for line in file]
                    self.exist_pid = set(self.exist_pid)
            #self.pixiv_pid(self.exist_pid)
            if not os.path.exists(self.path+r"/following.txt"):
                print("找不到following文件")
            else:
                with open((self.path+r"/following.txt")) as file:     #讀取寫入的文檔
                    self.Author_list = [line.rstrip() for line in file]
            if os.path.isfile(self.path+'data.json'):
                try:
                    with open(self.path+'data.json') as f:
                        data = json.load(f)
                        self.download_path=data['user_download_path']
                except:
                    print("加載user_data文件失敗\n重新選擇資料夾")
                    self.download_path=self.open_folder()
                    write_data(self)
            else :
                print("找不到user_data文件")
                self.download_path=self.open_folder()
                write_data(self)
        def write_data(self):
            jsonObject = {
            "user_download_path": self.download_path,    
            }
            user_data=self.path
            fileName = user_data+"data.json"
            file = open(fileName, "w")
            json.dump(jsonObject, file, indent = 4)
            file.close()        
        load_data(self)
        def Userinfo():
            try:
                user_data=os.getenv('APPDATA')+r'\twiter_download/'
                if os.path.isfile(user_data+'data.json'):
                    with open(user_data+'data.json') as f:
                        data = json.load(f)
                    address=data['email']
                    user_name=data['username']
                    link=data['last_time_url']
                    password=data['password']
                    download_path=data['usr_path']
            except:
                pass

            
    def set_cookies(self):          #設定cookies
        def read_cookies(self):         #讀取寫入的cookies
            with open((self.path+r"/cookies.txt")) as file:     #讀取寫入的文檔
                lines = [line.rstrip() for line in file]
            #print(len(lines))
            try:
                self.Agent=lines[0]
                self.cookies=lines[1:]
            except:
                self.get_cookies()
        
        def write_cookies(self):        #寫入獲得的cookies
            f = open((self.path+r"/cookies.txt"), "w")
            f.write(str(self.Agent)+'\n')
            #print(self.cookies)
            if self.cookies!=[]:
                for i in self.cookies:
                    f.write(str(i)+'\n')
            f.close()

        def get_cookies(self):
            with open((self.path+r"/password.txt")) as file:     #讀取寫入的文檔
                texts = [line.rstrip() for line in file]
            for i in range(0,len(texts),2):
                cookie,agent=pixiv_api.auto_get_cookie(texts[i],texts[i+1])
                self.cookies.append(cookie)
                self.Agent=agent

        if not os.path.exists(self.path+r"/cookies.txt"):
            print("找不到cookies文件")
            get_cookies(self)
            write_cookies(self)
        else:
            read_cookies(self)
            i,self.coookies=pixiv_api.Test_cookies(self.cookies,self.Agent)
            if(i<3):
                get_cookies(self)
                write_cookies(self)
        
    
    def open_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self,
                  "Open folder",
                  "./")                 # start path
        print(folder_path)
        path=folder_path
        return path
        #self.ui.show_folder_path.setText(folder_path)
    def set_bar(self, i):
        self.progressBar.setValue(i)
    def get_pixiv_author(self,path):
        all_pixiv_ids = pixiv_api.illusts('21971914'
        ,self.cookies[0]
        ,self.Agent)
        f = open((path+"/following.txt"), "w+")
        texts = np.unique(all_pixiv_ids).tolist()
        for text in texts:
            f.write(str(text)+'\n')
        f.close()
    
    def in_the_pid(id):
        if 'p' in id:
            pid=id.split('p')[0]
        else:
            pid=id
        if(len(pid)==6):
            if id in globals()['exist_pid'+str(pid[0])+"_"+str(6)]:
                return 0
            else:
                return 1
        elif(len(pid)==7):
            if id in globals()['exist_pid'+str(pid[0])+"_"+str(7)]:
                return 0
            else:
                return 1
        elif(len(pid)==8):
            if id in globals()['exist_pid'+str(pid[0])+"_"+str(8)]:
                return 0
            else:
                return 1
        elif(len(pid)==9):
            if id in globals()['exist_pid'+str(pid[0])+"_"+str(9)]:
                return 0
            else:
                return 1
    def pixiv_pid(self,exist_pid):
        for i in range(1,10):    
            for j in range(6,10):
                globals()['exist_pid'+str(i)+"_"+str(j)]=[]   
        for i in trange (0,len(exist_pid)):
            if 'p' in exist_pid[i]:
                pid=exist_pid[i].split('p')[0]
            else:
                pid=exist_pid[i]
            if(len(pid)==6):
                globals()['exist_pid'+str(exist_pid[i][0])+"_"+str(6)].append(exist_pid[i])
            elif(len(pid)==7):
                globals()['exist_pid'+str(exist_pid[i][0])+"_"+str(7)].append(exist_pid[i])
            elif(len(pid)==8):
                globals()['exist_pid'+str(exist_pid[i][0])+"_"+str(8)].append(exist_pid[i])
            else:
                globals()['exist_pid'+str(exist_pid[i][0])+"_"+str(9)].append(exist_pid[i])
        for i in range(1,10):    
            for j in range(6,10):
                globals()['exist_pid'+str(i)+"_"+str(j)]=str(globals()['exist_pid'+str(i)+"_"+str(j)])   
    def get_pixiv_author_imgID(self,path):
        with open((path+r"/following.txt")) as file:     #讀取寫入的文檔
            lines = [line.rstrip() for line in file]
        Author_list = np.array_split(lines,10)   #將獲取的文檔分成100等分
        for i in range(0,len(Author_list)):    
            locals()['picture_ids'+str(i)]=Queue()   
        threads=[]
        for i in range(0,len(Author_list)):
        #print(len(Author_list))
            threads.append(threading.Thread(target = pixiv_api.no_use_seleium_get_pid, 
            args = (Author_list[i],cookies[random.randint(0,len(cookies)-1)],Agent,locals()['picture_ids'+str(i)],self.path,i,str(self.exist_pid))))
            threads[i].start()
            while (threading.active_count())>5:
                    time.sleep(0.01)
        for i in range(8,len(Author_list)):
            threads[i].join()    
            f = open((path+r"/picture_ids"+str(i)+".txt"), "w+")
            texts=locals()['picture_ids'+str(i)].get()
            for text in texts:
                f.write(str(text)+'\n')
            f.close()