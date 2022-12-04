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

from Ui2 import Ui_MainWindow

global cookies
global Agent
global path
import concurrent.futures
import time
from datetime import datetime    
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QFileDialog

import pixiv_api
import pixiv_thread
from user_info import *


class Runthread(QtCore.QThread):
    #  通過類成員物件定義信號物件
    _signal = pyqtSignal(str)

    def __init__(self):
        super(Runthread, self).__init__()

    def __del__(self):
        self.wait()

    def run(self):
        for i in range(100):
            time.sleep(0.2)
            self._signal.emit(int(i))  # 注意這裡與_signal = pyqtSignal(str)中的類型相同

class MainWindow_controller(QtWidgets.QMainWindow):
    path='none'
    download_path=None
    exist_pid=''
    Author_list=[]
    cookies=""
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36 Edg/96.0.1054.62'
    picture_ids=Queue()
    i=-1
    exist_pid='unknown'
    start=0
    stop=100
    ban_tag=[]
    must_tag=[]
    like_num=0
    status = pyqtSignal(int)
    userdata_controller=''
    hide_accept=0
    NO_gif=0
    NO_tag=0
    NO_time=0
    last_download_time=""
    userid=""
    mode=0
    qmut_1 = QMutex() # 创建线程锁
    qmut_2 = QMutex()
    qmut_3 = QMutex()
    qmut_4 = QMutex()
    def __init__(self):
        super().__init__() # in python3, super(Class, self).xxx = super().xxx
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.thread1 = None  # 初始化執行緒
        self.setup_control()
    def disable_button(self):
        self.ui.get_following.setDisabled(True)
        self.ui.get_pid.setDisabled(True)
        self.ui.get_url.setDisabled(True)
        self.ui.download_url.setDisabled(True)
        self.ui.all_start.setDisabled(True)
    def enable_button(self):
        self.ui.get_following.setDisabled(False)
        self.ui.get_pid.setDisabled(False)
        self.ui.get_url.setDisabled(False)
        self.ui.download_url.setDisabled(False)
        self.ui.all_start.setDisabled(False)
    def add_output(self,out_str):
        self.ui.output.append(out_str)
        self.ui.output.moveCursor(QTextCursor.End)       
    def closeEvent(self,event):
        userdata_controller=Userdata_controller(self.path,
                                                    self.exist_pid,
                                                    self.Author_list,
                                                    self.download_path,
                                                    self.ban_tag,
                                                    self.must_tag,
                                                    self.like_num,
                                                    self.last_download_time,
                                                    self.ui.like_num,
                                                    self.ui.user_path1,
                                                    self.ui.download_time,
                                                    self.ui.ban_tag_list,
                                                    self.ui.must_tag_list
                                                    )
        userdata_controller.write_data()
        logging_mode_set(self.mode,self.ui.radioButton,self.ui.radioButton_2,self.ui.radioButton_3).write_data()
        cookies_set(self.cookies,self.Agent,self.userid,self.ui.account1,self.ui.password1,self.mode).write_cookies()
        othersettings(self.ui.hidefollow,self.ui.nogif,self.ui.notag,self.ui.notime,self.ui.create_dir,self.ui.no_R18G_dir).write_other_date()
        event.accept()
    def ui_cookies(self):
        if(self.cookies==[]):
            if self.ui.account1.text()=='' or self.ui.password1.text()=='':
                QMessageBox.warning(None, '錯誤', '帳號或密碼不得為空')
                return 
            else:
                cookies=cookies_set(self.cookies,self.Agent,self.userid,self.ui.account1,self.ui.password1,self.mode)
                self.userid,self.cookies,self.Agent=cookies.get_cookies()
    def notice(self,message):
        QMessageBox.warning(None, '完成', message)         
        if(message=='獲得關注帳號畫師完成'):
            self.enable_button()
        
    def setup_control(self):
        # TODO
        self.userdata_controller=Userdata_controller(self.path,
                                                    self.exist_pid,
                                                    self.Author_list,
                                                    self.download_path,
                                                    self.ban_tag,
                                                    self.must_tag,
                                                    self.like_num,
                                                    self.last_download_time,
                                                    self.ui.like_num,
                                                    self.ui.user_path1,
                                                    self.ui.download_time,
                                                    self.ui.ban_tag_list,
                                                    self.ui.must_tag_list
                                                    )

        #讀取用戶選擇的登入方式
        loggingmode=logging_mode_set(self.mode,self.ui.radioButton,self.ui.radioButton_2,self.ui.radioButton_3)
        self.mode=loggingmode.load_data()
        self.path,self.download_path,self.exist_pid,self.user_path1,self.Author_list,self.ban_tag,self.must_tag =self.userdata_controller.load_data()

        #讀取cookies檔案
        userid,cookies,agent=cookies_set(self.cookies,self.Agent,self.userid,self.ui.account1,self.ui.password1,self.mode).read_cookies()
        if(cookies!=0 and cookies != "" and agent!=0 and userid!=0):
            self.cookies=cookies
            self.Agent,self.userid=agent,userid
            self.ui.output.append('加載'+self.userid+'cookies完成')
            self.ui.output.moveCursor(QTextCursor.End)  
        else:
            self.ui.output.append('無法加載cookies'+self.userid+'加載完成')
        #設定雜項
        othersettings(self.ui.hidefollow,self.ui.nogif,self.ui.notag,self.ui.notime,self.ui.create_dir,self.ui.no_R18G_dir).setinfo()

        self.path=os.getenv('APPDATA')+r'/pixiv_download/'
        
        self.ui.change1.clicked.connect(lambda:self.userdata_controller.set_downloaa_path())             
        #self.ui.get_pid.clicked.connect(self.get_pixiv_author_button)
        #self.ui.get_url.clicked.connect(self.get_url_button)
        #self.ui.download_url.clicked.connect(lambda:download_img.download_img_main(self.download_path,self.start,self.stop,self.cookies,self.Agent))                                                             
        self.ui.ban_tag_input.returnPressed.connect(self.on_add_ban_tag_clicked)
        self.ui.must_tag_input.returnPressed.connect(self.on_add_must_tag_clicked)
        #self.ui.get_following.clicked.connect(self.buttonclick)
        
        #self.ui.getpixiv_author.clicked.connect(lambda:self.get_pixiv_author(self.path))
        #self.ui.label.setText('Happy World!')
    @QtCore.pyqtSlot()
    def on_pause_all_clicked (self):
        self.thread1.pause()

    @QtCore.pyqtSlot()    
    def on_continue_2_clicked(self):
        self.thread1.resume()
    @QtCore.pyqtSlot()    
    def on_stop_clicked(self):
        self.thread1.stop()
    
    @QtCore.pyqtSlot()
    def on_add_ban_tag_clicked(self):
        tag=self.ui.ban_tag_input.text()
        if(tag !=""):
            self.ui.ban_tag_input.setText("")
            self.ban_tag.append(tag)
            self.ui.ban_tag_list.addItem(QListWidgetItem(tag)) 
    
        
    @QtCore.pyqtSlot()
    def on_remove1_clicked(self):
        choose_item = self.ui.ban_tag_list.currentIndex()
        del self.ban_tag[choose_item.row()]
        self.ui.ban_tag_list.takeItem(choose_item.row())
        

    @QtCore.pyqtSlot()
    def on_add_must_tag_clicked(self):
        tag=self.ui.must_tag_input.text()
        if(tag !=""):
            self.ui.must_tag_input.setText("")
            self.must_tag.append(tag)
            self.ui.must_tag_list.addItem(QListWidgetItem(tag))    

    @QtCore.pyqtSlot()
    def on_remove2_clicked(self):
        choose_item = self.ui.must_tag_list.currentIndex()
        del self.must_tag[choose_item.row()]
        self.ui.must_tag_list.takeItem(choose_item.row())  
        
    @QtCore.pyqtSlot()
    def on_radioButton_clicked(self):
        print('本家')  
        self.mode=0

    @QtCore.pyqtSlot()
    def on_radioButton_3_clicked(self):
        print('FB')
        self.mode=1 

    @QtCore.pyqtSlot()
    def on_radioButton_2_clicked(self):
        print('Google')
        self.mode=2 
    
    @QtCore.pyqtSlot()
    def on_get_following_clicked(self):
        self.disable_button()
        self.ui_cookies()
        self.ui.progressBar.reset()
        #print(self.ui.hidefollow.isChecked())
        self.thread1=pixiv_thread.get_following(self.userid,self.cookies,self.Agent,self.ui.hidefollow,self.qmut_1,self.ui.output)
        # 連接信號
        self.thread1._signal.connect(self.progress_changed)
        self.thread1._output.connect(self.add_output)
        self.thread1._finished.connect(self.notice)
        self.thread1.start()
        

    @QtCore.pyqtSlot()
    def on_get_pid_clicked(self):
        self.ui_cookies()
        self.ui.progressBar.reset()
        # 創建執行緒
        self.thread1 = pixiv_thread.get_pixiv_author_imgID_Thread(self.Author_list,self.Agent,self.path,self.cookies,self.exist_pid)
        # 連接信號                                                
        self.thread1._signal.connect(self.progress_changed)  # 進程連接回傳到GUI的事件
        self.thread1._output.connect(self.add_output)
        self.thread1._finished.connect(self.notice)
        #print(self.exist_pid)
        # 開始執行緒
        self.thread1.start()

    def test(self):
        # 創建執行緒
        self.thread1 = pixiv_thread.test_thread()
        # 連接信號
        self.thread1.valueChange.connect(self.progress_changed)  # 進程連接回傳到GUI的事件
        
        # 開始執行緒
        self.thread1.start()
        while not self.thread1.isFinished():
            QThread.msleep(1)
    
    @QtCore.pyqtSlot()
    def on_get_url_clicked(self):
        self.ui_cookies()
        # 創建執行緒
        self.thread1 = pixiv_thread.get_img_url_thread(self.Author_list,self.Agent,self.cookies,self.exist_pid,self.ban_tag,self.must_tag,self.ui.like_num.value())
        # 連接信號
        self.thread1._signal.connect(self.progress_changed)  # 進程連接回傳到GUI的事件
        self.thread1._output.connect(self.add_output)
        self.thread1._finished.connect(self.notice)        
        # 開始執行緒
        self.thread1.start()

    @QtCore.pyqtSlot()
    def on_download_url_clicked(self):
        self.thread1 = pixiv_thread.download_thread(self.ui.nogif.isChecked(),self.ui.notag.isChecked(),self.ui.notime.isChecked(),self.ui.create_dir.isChecked(),self.ui.user_path1.text(),self.cookies,self.Agent,datetime.strptime(self.ui.download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss"),'%Y-%m-%d %H:%M:%S'),self.ui.no_R18G_dir.isChecked())
        self.thread1._signal.connect(self.progress_changed)  # 進程連接回傳到GUI的事件
        self.thread1._output.connect(self.add_output)
        self.thread1._finished.connect(self.notice)
        self.thread1._timechanged.connect(self.timechanged)
    def timechanged(self,mytime):
        mytime=datetime.strftime(mytime,'%Y-%m-%d %H:%M:%S')
        self.ui.download_time.setDateTime(QDateTime.fromString(self.last_download_time, "yyyy-MM-dd hh:mm:ss"))
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