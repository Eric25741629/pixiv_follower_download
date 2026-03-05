
import gdown
from user_info import *
import pixiv_thread
from PyQt5.QtCore import QThread, pyqtSignal
from datetime import datetime
import os
import time
from queue import Queue

import numpy as np
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from PyQt5.QtWidgets import QFileDialog

from Ui2 import Ui_MainWindow
import sys
import traceback
import pixiv_api
global cookies
global Agent
global path


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
    path = 'none'
    download_path = None
    exist_pid = ''
    Author_list = []
    cookies = ""
    Agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0'
    picture_ids = Queue()
    i = -1
    exist_pid = 'unknown'
    start = 0
    stop = 100
    ban_tag = []
    must_tag = []
    like_num = 0
    status = pyqtSignal(int)
    userdata_controller = ''
    hide_accept = 0
    NO_gif = 0
    NO_tag = 0
    NO_time = 0
    last_download_time = ""
    userid = ""
    mode = 0

    def __init__(self):
        super().__init__()  # in python3, super(Class, self).xxx = super().xxx
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        try:
            # 限制 QTextBrowser 保留的最大區塊數，避免累積大量行造成記憶體/渲染問題
            self.ui.output.document().setMaximumBlockCount(2000)
        except Exception:
            pass
        self.thread1 = None  # 初始化執行緒
        self._countdown_output_active = False
        self._countdown_block_number = None
        self._last_countdown_second = None

    def update(self):
        output = os.getenv('APPDATA')+r'/pixiv_download/update.txt'
        url = 'https://drive.google.com/u/1/uc?id=1uGppiPKA6TF0Zxz3SjCnAp_5_0YsPXYF&export=download'
        gdown.download(url, output)
        with open(output, encoding="utf-8") as file:  # 讀取寫入的文檔
            data = json.load(file)

        msgBox = QMessageBox()
        msgBox.setTextFormat(Qt.RichText)
        msgBox.setText(
            "有新的更新   <a href='https://drive.google.com/file/d/1zZF9kFVCP8HuwbZLu6sqEiyQmaEbuUSJ/view?usp=share_link'>按我跳轉至下載頁面</a>.")
        msgBox.setWindowTitle("檢測到新的版本")
        msgBox.exec()
        '''output=self.path+r'/check.json'

        with open(output, encoding="utf-8") as file:     #讀取寫入的文檔
            data = json.load(file)
        vision=float(data['updata_vision'])
        if vision>0.15:
            self.label.setOpenExternalLinks(True)
            self.label.setText(u'<a href="https://datutu.blog.csdn.net/" style="color:#0000ff;"><b> 我的CSDN博客 </b></a>')'''

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

    def add_output(self, out_str):
        # Append to GUI
        try:
            self._countdown_output_active = False
            self._countdown_block_number = None
            self._last_countdown_second = None
            self.ui.output.append(out_str)
            self.ui.output.moveCursor(QTextCursor.End)
        except Exception:
            pass
        # Also write a plain-text log (strip HTML tags) with timestamp
        try:
            import re
            log_text = re.sub(r'<[^>]+>', '', out_str)
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            user_data_path = os.getenv('APPDATA')+r'/pixiv_download/'
            os.makedirs(user_data_path, exist_ok=True)
            date_str = datetime.now().strftime('%Y-%m-%d')
            logfile = os.path.join(user_data_path, f'log-{date_str}.txt')
            with open(logfile, 'a', encoding='utf-8') as f:
                f.write(f'[{ts}] {log_text}\n')
        except Exception:
            pass

    def closeEvent(self, event):
        userdata_controller = Userdata_controller(self.path,
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
        try:
            userdata_controller.write_data()
        except Exception as e:
            try:
                self.ui.output.append('寫入 user data 失敗: ' + str(e))
            except Exception:
                pass
        try:
            if self.thread1 != None:
                if self.thread1.isRunning():
                    self.thread1.stop()
        except Exception:
            pass

        try:
            logging_mode_set(self.mode, self.ui.radioButton,
                             self.ui.radioButton_2, self.ui.radioButton_3).write_data()
        except Exception as e:
            try:
                self.ui.output.append('寫入 logging mode 失敗: ' + str(e))
            except Exception:
                pass

        try:
            cookies_set(self.cookies, self.Agent, self.userid,
                        self.ui.account1, self.ui.password1, self.mode).write_cookies()
        except Exception as e:
            try:
                self.ui.output.append('寫入 cookies 失敗: ' + str(e))
            except Exception:
                pass

        try:
            othersettings(self.ui.hidefollow, self.ui.nogif, self.ui.notag, self.ui.notime,
                          self.ui.create_dir, self.ui.no_R18G_dir,
                          getattr(self.ui, 'single_thread_mode', None),
                          getattr(self.ui, 'pid_wait_min', None),
                          getattr(self.ui, 'pid_wait_max', None)).write_other_date()
        except Exception as e:
            try:
                self.ui.output.append('寫入 other settings 失敗: ' + str(e))
            except Exception:
                pass

        try:
            userpass(self.ui.pass_tag, self.ui.pass_like).write()
        except Exception as e:
            try:
                self.ui.output.append('寫入 pass 設定失敗: ' + str(e))
            except Exception:
                pass

        event.accept()

    def ui_cookies(self):
        print(self.cookies)
        print(f"[controller] ui_cookies: current mode={self.mode}")
        if (self.cookies == ""):
            if self.ui.account1.text() == '' or self.ui.password1.text() == '':
                QMessageBox.warning(None, '錯誤', '帳號或密碼不得為空')
                return
            else:
                cookies = cookies_set(
                    self.cookies, self.Agent, self.userid, self.ui.account1, self.ui.password1, self.mode)
                self.userid, self.cookies, self.Agent = cookies.get_cookies()

    def log_start(self, text):
        try:
            self.ui.output.append('開始: ' + text)
            print('[controller] ' + text + ' started')
            self.ui.output.moveCursor(QTextCursor.End)
        except Exception:
            try:
                print('[controller] ' + text + ' started (ui append failed)')
            except Exception:
                pass

    @QtCore.pyqtSlot(int)
    def update_countdown(self, seconds):
        try:
            # 在狀態列只顯示剩餘秒數，避免大量文字刷屏
            self.statusBar().showMessage(f"等待 {seconds} 秒...")
        except Exception:
            pass
        # 依需求：直接覆寫輸出框「最後一行」倒數，不做文字比對
        try:
            if self._last_countdown_second == seconds:
                return
            self._last_countdown_second = seconds
            text = f"等待 {seconds} 秒..." if seconds > 0 else "等待結束"

            if not self._countdown_output_active:
                self.ui.output.append(text)
                self._countdown_output_active = True
            else:
                cursor = self.ui.output.textCursor()
                cursor.movePosition(QTextCursor.End)
                cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                cursor.insertText(text)
                self.ui.output.setTextCursor(cursor)

            if seconds <= 0:
                self._countdown_output_active = False
                self._countdown_block_number = None
                self._last_countdown_second = None
            self.ui.output.moveCursor(QTextCursor.End)
        except Exception:
            pass

    def enable_thread_controls(self):
        try:
            self.ui.pause_all.setDisabled(False)
            self.ui.continue_2.setDisabled(True)
            self.ui.stop.setDisabled(False)
        except Exception:
            pass

    def disable_thread_controls(self):
        try:
            self.ui.pause_all.setDisabled(True)
            self.ui.continue_2.setDisabled(True)
            self.ui.stop.setDisabled(True)
        except Exception:
            pass

    def notice(self, message):
        QMessageBox.warning(None, '完成', message)
        # 當執行緒完成時停用暫停/繼續/中止按鈕
        self.disable_thread_controls()
        self.enable_button()
        try:
            self.statusBar().clearMessage()
        except Exception:
            pass

    def setup_control(self):
        # self.update()
        # TODO
        self.userdata_controller = Userdata_controller(self.path,
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

        # 讀取用戶選擇的登入方式
        # logging_mode_set expects args: (mode, _ui_pixiv_mode, _ui_fb_mode, _ui_google_mode)
        # UI object names: radioButton = pixiv, radioButton_2 = Google, radioButton_3 = FB
        # pass radioButton_3 (FB) as _ui_fb_mode and radioButton_2 (Google) as _ui_google_mode
        loggingmode = logging_mode_set(
            self.mode, self.ui.radioButton, self.ui.radioButton_3, self.ui.radioButton_2)
        self.mode = loggingmode.load_data()
        self.path, self.download_path, self.exist_pid, self.user_path1, self.Author_list, self.ban_tag, self.must_tag = self.userdata_controller.load_data()

        # 讀取cookies檔案
        userid, cookies, agent = cookies_set(
            self.cookies, self.Agent, self.userid, self.ui.account1, self.ui.password1, self.mode).read_cookies()
        if (cookies != 0 and cookies != "" and agent != 0 and userid != 0):
            self.cookies = cookies
            self.Agent, self.userid = agent, userid
            self.ui.output.append('加載'+self.userid+'cookies完成')
            self.ui.output.moveCursor(QTextCursor.End)
        else:
            self.ui.output.append('無法加載cookies'+self.userid+'')
        # 設定雜項
        othersettings(self.ui.hidefollow, self.ui.nogif, self.ui.notag,
                  self.ui.notime, self.ui.create_dir, self.ui.no_R18G_dir,
                  getattr(self.ui, 'single_thread_mode', None),
                  getattr(self.ui, 'pid_wait_min', None),
                  getattr(self.ui, 'pid_wait_max', None)).setinfo()
        userpass(self.ui.pass_tag, self.ui.pass_like).read_info()
        self.path = os.getenv('APPDATA')+r'/pixiv_download/'

        self.ui.change1.clicked.connect(
            lambda: self.userdata_controller.set_downloaa_path())

        # cookies paste/save button
        try:
            self.ui.saveCookiesBtn.clicked.connect(self.on_save_cookies_clicked)
        except Exception:
            pass

        self.ui.ban_tag_input.returnPressed.connect(
            self.on_add_ban_tag_clicked)
        self.ui.must_tag_input.returnPressed.connect(
            self.on_add_must_tag_clicked)
        try:
            self.test_cookies()
        except Exception as e:
            try:
                self.ui.output.append('test_cookies 發生錯誤: ' + str(e))
            except Exception:
                pass

    def test_cookies(self):
        test, cookies = pixiv_api.Test_cookies([self.cookies], self.Agent)
        print(test)
        if test == 0:
            cookies = cookies_set(self.cookies, self.Agent, self.userid,
                                  self.ui.account1, self.ui.password1, self.mode)
            # self.userid, self.cookies, self.Agent = cookies.get_cookies()

    @QtCore.pyqtSlot()
    def on_pause_all_clicked(self):
        if not self.thread1:
            QMessageBox.warning(None, '錯誤', '沒有正在執行的工作可暫停')
            return
        try:
            if hasattr(self.thread1, 'pause'):
                self.thread1.pause()
                try:
                    self.ui.pause_all.setDisabled(True)
                    self.ui.continue_2.setDisabled(False)
                except Exception:
                    pass
            else:
                QMessageBox.warning(None, '錯誤', '當前執行緒不支援暫停')
        except Exception as e:
            QMessageBox.warning(None, '錯誤', str(e))

    @QtCore.pyqtSlot()
    def on_continue_2_clicked(self):
        if not self.thread1:
            QMessageBox.warning(None, '錯誤', '沒有正在執行的工作可繼續')
            return
        try:
            if hasattr(self.thread1, 'resume'):
                self.thread1.resume()
                try:
                    self.ui.pause_all.setDisabled(False)
                    self.ui.continue_2.setDisabled(True)
                except Exception:
                    pass
            else:
                QMessageBox.warning(None, '錯誤', '當前執行緒不支援繼續')
        except Exception as e:
            QMessageBox.warning(None, '錯誤', str(e))

    @QtCore.pyqtSlot()
    def on_stop_clicked(self):
        if not self.thread1:
            QMessageBox.warning(None, '錯誤', '沒有正在執行的工作可中止')
            return
        try:
            if hasattr(self.thread1, 'stop'):
                self.thread1.stop()
                # 停止後停用所有控制按鈕
                try:
                    self.disable_thread_controls()
                    self.disable_button()
                except Exception:
                    pass
            else:
                QMessageBox.warning(None, '錯誤', '當前執行緒不支援中止')
        except Exception as e:
            QMessageBox.warning(None, '錯誤', str(e))

    @QtCore.pyqtSlot()
    def on_add_ban_tag_clicked(self):
        tag = self.ui.ban_tag_input.text()
        if (tag != "" and not tag.isspace()):
            self.ui.ban_tag_input.setText("")
            self.ban_tag.append(tag)
            self.ui.ban_tag_list.addItem(QListWidgetItem(tag))

    @QtCore.pyqtSlot()
    def on_remove1_clicked(self):
        choose_item = self.ui.ban_tag_list.currentIndex()
        del self.ban_tag[choose_item.row()]
        self.ui.ban_tag_list.takeItem(choose_item.row())

    def output_err(self, e):
        error_class = e.__class__.__name__  # 取得錯誤類型
        detail = e.args[0]  # 取得詳細內容
        cl, exc, tb = sys.exc_info()  # 取得Call Stack
        lastCallStack = traceback.extract_tb(tb)[-1]  # 取得Call Stack的最後一筆資料
        fileName = lastCallStack[0]  # 取得發生的檔案名稱
        lineNum = lastCallStack[1]  # 取得發生的行號
        funcName = lastCallStack[2]  # 取得發生的函數名稱
        errMsg = "File \"{}\",  in {}: [{}] ".format(
            fileName, funcName, error_class)
        return (errMsg)

    @QtCore.pyqtSlot()
    def on_remove_tag_recorder_clicked(self):
        try:
            file_name = "tag_ban_pid.txt"
            os.remove(self.path+file_name)
            QMessageBox.warning(None, '完成', '已刪除')
        except Exception as e:
            # self.add_output((self.output_err(e)))
            error_class = e.__class__.__name__  # 取得錯誤類型
            QMessageBox.warning(None, '錯誤', error_class)

    @QtCore.pyqtSlot()
    def on_relogging_clicked(self):
        try:
            cookies = cookies_set(self.cookies, self.Agent, self.userid,
                                  self.ui.account1, self.ui.password1, self.mode)
            self.userid, self.cookies, self.Agent = cookies.get_cookies()
        except Exception as e:
            # self.add_output((self.output_err(e)))
            error_class = e.__class__.__name__  # 取得錯誤類型
            QMessageBox.warning(None, '錯誤', error_class)

    @QtCore.pyqtSlot()
    def on_save_cookies_clicked(self):
        try:
            raw = self.ui.cookies_input.text()
            # 清理開頭/結尾空白與換行
            cookies_text = raw.strip().replace('\r', '').replace('\n', ' ').strip()
            # 嘗試分離可能被貼在尾端的 User-Agent（通常以 Mozilla/ 開頭）
            ua = None
            idx = cookies_text.find('Mozilla/')
            if idx != -1:
                ua = cookies_text[idx:]
                cookies_text = cookies_text[:idx].strip()
                cookies_text = cookies_text.rstrip('; ').strip()
            if ua is None and ('\n' in raw or '\r' in raw or 'User-Agent:' in raw):
                parts = raw.replace('\r', '').split('\n')
                if len(parts) > 1:
                    last = parts[-1].strip()
                    if 'Mozilla/' in last or 'Chrome/' in last or 'Edge/' in last or 'Safari/' in last:
                        ua = last
                        cookies_text = '\n'.join(parts[:-1]).replace('\r', '').replace('\n', ' ').strip()
            if ua is None and '=' not in cookies_text and cookies_text:
                ua = cookies_text
                cookies_text = ''
            # 將清理後的 cookie 回寫到 UI
            self.ui.cookies_input.setText(cookies_text)
            user_data_path = os.getenv('APPDATA')+r'/pixiv_download/'
            if not os.path.exists(user_data_path):
                os.makedirs(user_data_path, exist_ok=True)
            fileName = os.path.join(user_data_path, 'cookies.json')
            agent_to_save = ua or self.Agent
            data = {
                "agent": agent_to_save,
                "userid": self.userid or "",
                "account": self.ui.account1.text(),
                "password": self.ui.password1.text(),
                "cookies": cookies_text
            }
            with open(fileName, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.cookies = cookies_text
            if ua:
                self.Agent = ua
            self.ui.output.append('已儲存 cookies 至設定檔')
        except Exception as e:
            QMessageBox.warning(None, '錯誤', str(e))

    @QtCore.pyqtSlot()
    def on_remove_like_num_clicked(self):
        try:
            file_name = "pid_num_pid.txt"
            os.remove(self.path+file_name)
            QMessageBox.warning(None, '完成', '已刪除')
        except Exception as e:
            # self.add_output((self.output_err(e)))
            error_class = e.__class__.__name__  # 取得錯誤類型
            QMessageBox.warning(None, '錯誤', error_class)

    @QtCore.pyqtSlot()
    def on_add_must_tag_clicked(self):
        tag = self.ui.must_tag_input.text()
        if (tag != "" and not tag.isspace()):
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
        self.mode = 0

    @QtCore.pyqtSlot()
    def on_radioButton_3_clicked(self):
        print('FB')
        self.mode = 2

    @QtCore.pyqtSlot()
    def on_radioButton_2_clicked(self):
        print('Google')
        self.mode = 1

    @QtCore.pyqtSlot()
    def on_get_following_clicked(self):
        self.disable_button()
        self.ui_cookies()
        self.ui.progressBar.reset()
        self.log_start('獲取關注畫師')
        # print(self.ui.hidefollow.isChecked())
        self.thread1 = pixiv_thread.get_following(
            self.userid, self.cookies, self.Agent, self.ui.hidefollow)
        self.enable_thread_controls()
        # 連接信號
        self.thread1._signal.connect(self.progress_changed)  # 進程連接回傳到GUI的事件
        self.thread1._output.connect(self.add_output)
        self.thread1._finished.connect(self.notice)
        # 開始執行緒
        self.thread1.start()
        self.enable_thread_controls()

    def test(self):
        # 創建執行緒
        self.thread1 = pixiv_thread.test_thread()
        # 連接信號
        self.thread1.valueChange.connect(
            self.progress_changed)  # 進程連接回傳到GUI的事件

        # 開始執行緒
        self.thread1.start()
        self.enable_thread_controls()
        while not self.thread1.isFinished():
            QThread.msleep(1)

    @QtCore.pyqtSlot()
    def on_get_pid_clicked(self):
        # Start fetching author PIDs (images IDs)
        self.disable_button()
        self.ui_cookies()
        self.log_start('獲取關注畫師的圖片ID')
        try:
            single_mode = self.ui.single_thread_mode.isChecked()
        except Exception:
            single_mode = False
        try:
            pid_wait_min = int(self.ui.pid_wait_min.value())
            pid_wait_max = int(self.ui.pid_wait_max.value())
        except Exception:
            pid_wait_min, pid_wait_max = 10, 60
        self.thread1 = pixiv_thread.get_pixiv_author_imgID_Thread(
            self.Author_list, self.Agent, self.path, self.cookies, self.exist_pid, single_mode, pid_wait_min, pid_wait_max)
        # connect signals
        self.thread1._signal.connect(self.progress_changed)
        self.thread1._output.connect(self.add_output)
        self.thread1._finished.connect(self.notice)
        if hasattr(self.thread1, '_countdown'):
            try:
                self.thread1._countdown.connect(self.update_countdown)
            except Exception:
                pass
        self.thread1.start()
        self.enable_thread_controls()

    @QtCore.pyqtSlot()
    def on_get_url_clicked(self):
        self.ui_cookies()
        # 創建執行緒
        self.disable_button()
        self.log_start('獲取圖片id的詳細資料')
        no_to_check = []
        if self.ui.pass_tag.isChecked():
            try:
                with open((self.path+r"/tag_ban_pid.txt")) as file:  # 讀取寫入的文檔
                    no_to_check += [line.rstrip() for line in file]
            except:
                pass
        if self.ui.pass_like.isChecked():
            try:
                with open((self.path+r"/pid_num_pid.txt")) as file:  # 讀取寫入的文檔
                    no_to_check += [line.rstrip() for line in file]
                    print(len(no_to_check))
                    no_to_check = set(no_to_check)
            except:
                pass
        try:
            single_mode = self.ui.single_thread_mode.isChecked()
        except Exception:
            single_mode = False
        try:
            pid_wait_min = int(self.ui.pid_wait_min.value())
            pid_wait_max = int(self.ui.pid_wait_max.value())
        except Exception:
            pid_wait_min, pid_wait_max = 10, 60
        self.thread1 = pixiv_thread.get_img_url_thread(
            self.Author_list, self.Agent, self.cookies, self.exist_pid, self.ban_tag, self.must_tag, self.ui.like_num.value(), no_to_check,
            single_mode, pid_wait_min, pid_wait_max)
        # 連接信號
        self.thread1._signal.connect(self.progress_changed)  # 進程連接回傳到GUI的事件
        self.thread1._output.connect(self.add_output)
        if hasattr(self.thread1, '_countdown'):
            try:
                self.thread1._countdown.connect(self.update_countdown)
            except Exception:
                pass
        self.thread1._finished.connect(self.notice)
        # 開始執行緒
        self.thread1.start()

    @QtCore.pyqtSlot()
    def on_download_url_clicked(self):
        self.ui_cookies()
        self.disable_button()
        try:
            self.ui.progressBar.reset()
            self.ui.progressBar.setValue(0)
        except Exception:
            pass
        self.log_start('開始下載')
        # single_thread_mode: 超慢速單執行緒，若勾選則下載使用單一 worker
        # 傳入 single_thread_mode 參數
        # NOTE: rebuild thread with single_thread_mode if UI checkbox present
        try:
            single_mode = self.ui.single_thread_mode.isChecked()
        except Exception:
            single_mode = False
        try:
            pid_wait_min = int(self.ui.pid_wait_min.value())
            pid_wait_max = int(self.ui.pid_wait_max.value())
        except Exception:
            pid_wait_min, pid_wait_max = 1, 3
        # recreate thread with single_mode
        self.thread1 = pixiv_thread.download_thread(self.ui.nogif.isChecked(),
                                                    self.ui.notag.isChecked(),
                                                    self.ui.notime.isChecked(),
                                                    self.ui.create_dir.isChecked(),
                                                    self.ui.user_path1.text(),
                                                    self.cookies, self.Agent,
                                                    datetime.strptime(self.ui.download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss"),
                                                                      '%Y-%m-%d %H:%M:%S'), self.ui.no_R18G_dir.isChecked(), single_mode, pid_wait_min, pid_wait_max)
        self.thread1._signal.connect(self.progress_changed)  # 進程連接回傳到GUI的事件
        self.thread1._output.connect(self.add_output)
        if hasattr(self.thread1, '_countdown'):
            try:
                self.thread1._countdown.connect(self.update_countdown)
            except Exception:
                pass
        self.thread1._finished.connect(self.notice)
        self.thread1._timechanged.connect(self.timechanged)
        self.thread1.start()
        self.enable_thread_controls()

    @QtCore.pyqtSlot()
    def on_all_start_clicked(self):
        self.disable_button()
        self.ui_cookies()
        self.ui.progressBar.reset()
        self.log_start('一鍵開始')
        # print(self.ui.hidefollow.isChecked())
        self.thread1 = pixiv_thread.get_following(
            self.userid, self.cookies, self.Agent, self.ui.hidefollow)
        self.enable_thread_controls()
        # 連接信號
        self.thread1._signal.connect(self.progress_changed)
        self.thread1._output.connect(self.add_output)
        self.thread1._thenext.connect(self.the_next)
        if hasattr(self.thread1, '_countdown'):
            try:
                self.thread1._countdown.connect(self.update_countdown)
            except Exception:
                pass
        self.thread1.start()

    def the_next(self, num):
        if (num == -1):
            self.enable_button()
            self.notice('已終止')
        elif (num == 2):
            self.log_start('獲取關注畫師的圖片ID')
            try:
                single_mode = self.ui.single_thread_mode.isChecked()
            except Exception:
                single_mode = False
            try:
                pid_wait_min = int(self.ui.pid_wait_min.value())
                pid_wait_max = int(self.ui.pid_wait_max.value())
            except Exception:
                pid_wait_min, pid_wait_max = 10, 60
            self.thread1 = pixiv_thread.get_pixiv_author_imgID_Thread(
                self.Author_list, self.Agent, self.path, self.cookies, self.exist_pid, single_mode, pid_wait_min, pid_wait_max)
            # 連接信號
            self.thread1._signal.connect(
                self.progress_changed)  # 進程連接回傳到GUI的事件
            self.thread1._output.connect(self.add_output)
            self.thread1._thenext.connect(self.the_next)
            if hasattr(self.thread1, '_countdown'):
                try:
                    self.thread1._countdown.connect(self.update_countdown)
                except Exception:
                    pass
            self.thread1.start()
            self.enable_thread_controls()
        elif (num == 3):
            self.ui_cookies()
            # 創建執行緒
            no_to_check = []
            if self.ui.pass_tag.isChecked():
                try:
                    with open((self.path+r"/tag_ban_pid.txt")) as file:  # 讀取寫入的文檔
                        no_to_check += [line.rstrip() for line in file]
                except:
                    pass
            if self.ui.pass_like.isChecked():
                try:
                    with open((self.path+r"/pid_num_pid.txt")) as file:  # 讀取寫入的文檔
                        no_to_check += [line.rstrip() for line in file]
                except:
                    pass
            try:
                single_mode = self.ui.single_thread_mode.isChecked()
            except Exception:
                single_mode = False
            try:
                pid_wait_min = int(self.ui.pid_wait_min.value())
                pid_wait_max = int(self.ui.pid_wait_max.value())
            except Exception:
                pid_wait_min, pid_wait_max = 10, 60
            self.thread1 = pixiv_thread.get_img_url_thread(
                self.Author_list, self.Agent, self.cookies, self.exist_pid, self.ban_tag, self.must_tag, self.ui.like_num.value(), no_to_check,
                single_mode, pid_wait_min, pid_wait_max)
            # 連接信號
            self.thread1._signal.connect(
                self.progress_changed)  # 進程連接回傳到GUI的事件
            self.thread1._output.connect(self.add_output)
            if hasattr(self.thread1, '_countdown'):
                try:
                    self.thread1._countdown.connect(self.update_countdown)
                except Exception:
                    pass
            self.thread1._thenext.connect(self.the_next)
            # self.thread1._finished.connect(self.notice)
            # 開始執行緒
            self.thread1.start()
            self.enable_thread_controls()
        elif (num == 4):
            self.on_download_url_clicked()

    def timechanged(self, mytime):
        dt = QDateTime.fromString(mytime, "yyyy-MM-dd hh:mm:ss")
        if dt.isValid():
            self.ui.download_time.setDateTime(dt)
            self.last_download_time = mytime
        else:
            # 保底：格式異常時仍用目前時間，避免欄位不更新
            self.ui.download_time.setDateTime(QDateTime.currentDateTime())
            self.last_download_time = self.ui.download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss")

        # 立即落盤，避免只在關閉程式時才寫入導致「上次下載時間」看起來沒更新
        try:
            if getattr(self, 'userdata_controller', None):
                self.userdata_controller.write_data()
        except Exception as e:
            try:
                self.ui.output.append('寫入下載時間失敗: ' + str(e))
            except Exception:
                pass

    def progress_changed(self, step, setmax):
        self.ui.progressBar.setMaximum(setmax)
        value = self.ui.progressBar.value() + step
        if (value > setmax):
            value = setmax
        self.ui.progressBar.setValue(value)
        try:
            self.ui.progressBar.setTextVisible(True)
            self.ui.progressBar.setFormat(f"已處理 {value}/{setmax}")
        except Exception:
            pass
        self.ui.progressBar.update()
        try:
            # 顯示已處理 / 總數於狀態列
            msg = f"已處理 {value}/{setmax}"
            self.statusBar().showMessage(msg)
            try:
                # 也寫入 QTextBrowser
                self.ui.output.append(msg)
            except Exception:
                pass
        except Exception:
            pass

    def open_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self,
                                                       "Open folder",
                                                       "./")                 # start path
        print(folder_path)
        path = folder_path
        return path
        # self.ui.show_folder_path.setText(folder_path)
