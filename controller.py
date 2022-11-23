import json
import multiprocessing as MP
import os
import random
import threading
import time
from functools import partial
from multiprocessing import Pool
from queue import Queue

import numpy as np
import requests
import tqdm
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from PyQt5.QtWidgets import QFileDialog
from tqdm import tqdm, trange
import download_url
import pixiv_api
from Ui2 import Ui_MainWindow

global cookies
global Agent
global path
import concurrent.futures
import time

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QFileDialog

import download_img
from user_info import Userdata_controller
import pixiv_thread


class Runthread(QtCore.QThread):
    #  通过类成员对象定义信号对象
    _signal = pyqtSignal(str)

    def __init__(self):
        super(Runthread, self).__init__()

    def __del__(self):
        self.wait()

    def run(self):
        for i in range(100):
            time.sleep(0.2)
            self._signal.emit(int(i))  # 注意这里与_signal = pyqtSignal(str)中的类型相同


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
        self.thread = None  # 初始化线程
        self.setup_control()
    def closeEvent(self,event):
        self.Userdata_controller.write_data()
        event.accept()
        
    def setup_control(self):
        # TODO

        #self.ui.actionfile.triggered.connect(self.open_folder) 
        #self.path=self.open_folder()
        self.Userdata_controller=Userdata_controller(self.path,
                                                    self.exist_pid,
                                                    self.Author_list,
                                                    self.download_path,
                                                    self.start,
                                                    self.stop,
                                                    self.ui.user_path1
                                                    )
        self.path,self.download_path,self.exist_pid,self.user_path1,self.Author_list ,self.start,self.stop=self.Userdata_controller.load_data()

        self.path=os.getenv('APPDATA')+r'/pixiv_download/'
        self.set_cookies()
        for i in range(len(self.cookies)):
            self.ui.output.setPlainText(self.cookies[i])
            self.ui.output.moveCursor(QTextCursor.End)      
        #self.ui.get_following.clicked.connect(lambda:self.get_pixiv_author(self.path))
        self.ui.get_pid.clicked.connect(self.get_pidbutton)
        self.ui.get_url.clicked.connect(lambda:download_url.main(self.cookies,self.Agent))
        self.ui.download_url.clicked.connect(lambda:download_img.download_img_main(self.download_path,self.start,self.stop,self.cookies,self.Agent))
        self.ui.change1.clicked.connect(lambda:self.Userdata_controller.set_downloaa_path())                                                                 
        
        self.ui.get_following.clicked.connect(self.buttonclick)
        
        #self.ui.getpixiv_author.clicked.connect(lambda:self.get_pixiv_author(self.path))
        #self.ui.label.setText('Happy World!')
        self.ui.begin_num.setValue(self.start)
        self.ui.spinBox_6.setValue(self.stop)   
        self.ui.begin_num.valueChanged.connect(self.showMsg)
    def showMsg(self):  
        self.start=(int(self.ui.begin_num.value()))
        self.ui.output.moveCursor(QTextCursor.End)     
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
    def get_pidbutton(self):
        # 创建线程
        
        self.thread = pixiv_thread.get_pixiv_author_imgID_Thread(self.Author_list,self.Agent,self.path,self.cookies,self.exist_pid)
        # 连接信号
        self.thread._signal.connect(self.progress_changed)  # 进程连接回传到GUI的事件
        # 开始线程
        self.thread.start()    
    def buttonclick(self):
        # 创建线程
        self.thread = Runthread()
        # 连接信号
        self.thread._signal.connect(self.progress_changed)  # 进程连接回传到GUI的事件
        # 开始线程
        self.thread.start()

    def progress_changed(self, now,max): 
        #value=now/max*100 
        self.ui.progressBar.setRange(0, max)      
        self.ui.progressBar.setValue(now)
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