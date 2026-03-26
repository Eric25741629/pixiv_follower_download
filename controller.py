
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
from run_actions import (
    start_get_following,
    start_get_pid,
    start_get_url,
    start_download,
    start_all,
    continue_all,
)
global cookies
global Agent
global path


class Runthread(QtCore.QThread):
    #  ???????????????????????????????
    _signal = pyqtSignal(str)

    def __init__(self):
        super(Runthread, self).__init__()

    def __del__(self):
        self.wait()

    def run(self):
        for i in range(100):
            time.sleep(0.2)
            self._signal.emit(int(i))  # ?????怏??????????????????nal = pyqtSignal(str)?????????桀???????


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
            # ???? QTextBrowser ????????????Ⅹ鞊???????????????????????????????????????????怏???????
            self.ui.output.document().setMaximumBlockCount(2000)
        except Exception:
            pass
        self.thread1 = None  # ??????????????????Ⅹ????
        self._countdown_output_active = False
        self._countdown_block_number = None
        self._last_countdown_second = None

    def update(self):
        output = os.getenv('APPDATA')+r'/pixiv_download/update.txt'
        url = 'https://drive.google.com/u/1/uc?id=1uGppiPKA6TF0Zxz3SjCnAp_5_0YsPXYF&export=download'
        gdown.download(url, output)
        with open(output, encoding="utf-8") as file:  # ????????????????????
            data = json.load(file)

        msgBox = QMessageBox()
        msgBox.setTextFormat(Qt.RichText)
        msgBox.setText(
            "??????????  <a href='https://drive.google.com/file/d/1zZF9kFVCP8HuwbZLu6sqEiyQmaEbuUSJ/view?usp=share_link'>??????????????????怏??????/a>.")
        msgBox.setWindowTitle("?????????????")
        msgBox.exec()
        '''output=self.path+r'/check.json'

        with open(output, encoding="utf-8") as file:     #????????????????????
            data = json.load(file)
        vision=float(data['updata_vision'])
        if vision>0.15:
            self.label.setOpenExternalLinks(True)
            self.label.setText(u'<a href="https://datutu.blog.csdn.net/" style="color:#0000ff;"><b> ???CSDN????????</b></a>')'''

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
            try:
                from safe_io import atomic_append_text
                atomic_append_text(logfile, f'[{ts}] {log_text}')
            except Exception:
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
                self.ui.output.append("Failed to save user data: " + str(e))
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
                self.ui.output.append("Failed to save logging mode: " + str(e))
            except Exception:
                pass

        try:
            cookies_set(self.cookies, self.Agent, self.userid,
                        self.ui.account1, self.ui.password1, self.mode).write_cookies()
        except Exception as e:
            try:
                self.ui.output.append("Failed to save cookies: " + str(e))
            except Exception:
                pass

        try:
            othersettings(self.ui.hidefollow, self.ui.nogif, self.ui.notag, self.ui.notime,
                          self.ui.create_dir, self.ui.no_R18G_dir,
                          getattr(self.ui, 'single_thread_mode', None),
                          getattr(self.ui, 'pid_wait_min', None),
                          getattr(self.ui, 'pid_wait_max', None),
                          getattr(self.ui, 'pid_wait_nocookie_min', None),
                          getattr(self.ui, 'pid_wait_nocookie_max', None),
                          getattr(self.ui, 'jxl_enable', None),
                          getattr(self.ui, 'jxl_cjxl_path', None),
                          getattr(self.ui, 'jxl_delete_original', None),
                          getattr(self.ui, 'jxl_effort', None)).write_other_date()
        except Exception as e:
            try:
                self.ui.output.append("Failed to save other settings: " + str(e))
            except Exception:
                pass

        try:
            userpass(self.ui.pass_tag, self.ui.pass_like).write()
        except Exception as e:
            try:
                self.ui.output.append("Failed to save pass settings: " + str(e))
            except Exception:
                pass

        event.accept()

    def ui_cookies(self):
        print(self.cookies)
        print(f"[controller] ui_cookies: current mode={self.mode}")
        if self.cookies == "":
            if self.ui.account1.text() == '' or self.ui.password1.text() == '':
                QMessageBox.warning(None, "Warning", "Please input account/password or paste cookies.")
                return
            cookies = cookies_set(
                self.cookies, self.Agent, self.userid, self.ui.account1, self.ui.password1, self.mode
            )
            self.userid, self.cookies, self.Agent = cookies.get_cookies()

    def log_start(self, text):
        try:
            self.ui.output.append('Start: ' + text)
            print('[controller] ' + text + ' started')
            self.ui.output.moveCursor(QTextCursor.End)
        except Exception:
            try:
                print('[controller] ' + text + ' started (ui append failed)')
            except Exception:
                pass

    def _setup_action_ui(self):
        try:
            self.ui.get_following.setText('Step 1: Get Following Artists')
            self.ui.get_pid.setText('Step 2: Get Artwork IDs')
            self.ui.get_url.setText('Step 3: Get Artwork Details')
            self.ui.download_url.setText('Step 4: Start Download')
            self.ui.all_start.setText('Run All (1->4)')
            self.ui.get_following.setToolTip('Sync your following artist list first')
            self.ui.get_pid.setToolTip('Fetch artwork IDs from following artists')
            self.ui.get_url.setToolTip('Resolve artwork URLs and metadata by IDs')
            self.ui.download_url.setToolTip('Download from all_url.txt')
            self.ui.all_start.setToolTip('Run steps 1 to 4 in sequence')
        except Exception:
            pass
        try:
            section_label = QLabel('Recommended: run Step 1,2,3 then Step 4.')
            section_label.setStyleSheet('color:#374151; padding:4px 2px;')
            self.ui.run_tab_actions_layout.insertWidget(0, section_label)
        except Exception:
            pass

    def _setup_jxl_ui(self):
        try:
            # Create JXL controls dynamically to avoid requiring .ui regeneration.
            group = QGroupBox('JXL 自動轉檔')
            layout = QGridLayout(group)

            self.ui.jxl_enable = QCheckBox('下載完成後自動轉為 JXL (無損)')
            self.ui.jxl_enable.setChecked(False)
            layout.addWidget(self.ui.jxl_enable, 0, 0, 1, 4)

            layout.addWidget(QLabel('cjxl 路徑'), 1, 0)
            self.ui.jxl_cjxl_path = QLineEdit(r'C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe')
            self.ui.jxl_cjxl_path.setPlaceholderText(r'C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe')
            layout.addWidget(self.ui.jxl_cjxl_path, 1, 1, 1, 2)

            self.ui.jxl_browse = QPushButton('瀏覽...')
            self.ui.jxl_browse.clicked.connect(self.on_browse_jxl_cjxl_clicked)
            layout.addWidget(self.ui.jxl_browse, 1, 3)

            layout.addWidget(QLabel('壓縮 effort (1-9)'), 2, 0)
            self.ui.jxl_effort = QSpinBox()
            self.ui.jxl_effort.setRange(1, 9)
            self.ui.jxl_effort.setValue(7)
            layout.addWidget(self.ui.jxl_effort, 2, 1)

            self.ui.jxl_delete_original = QCheckBox('轉檔成功後刪除原圖')
            self.ui.jxl_delete_original.setChecked(False)
            layout.addWidget(self.ui.jxl_delete_original, 2, 2, 1, 2)

            try:
                self.ui.run_tab_actions_layout.addWidget(group)
            except Exception:
                # Fallback: attach to central widget layout if available.
                container = self.ui.centralwidget.layout() if hasattr(self.ui, 'centralwidget') else None
                if container is not None:
                    container.addWidget(group)
        except Exception:
            pass

    def _print_backup_policy(self):
        try:
            self.ui.output.append('[Backup Policy]')
            self.ui.output.append('1) atomic_write_* backs up old file to sibling history/')
            self.ui.output.append('2) backup=False skips history (ex: cookies.json)')
            self.ui.output.append('3) Name: filename.YYYYMMDD(.N), keep latest 10')
        except Exception:
            pass

    @QtCore.pyqtSlot(int)
    def update_countdown(self, seconds):
        try:
            self.statusBar().showMessage(f"Waiting {seconds}s..." if seconds > 0 else "Wait complete")
        except Exception:
            pass
        try:
            if self._last_countdown_second == seconds:
                return
            self._last_countdown_second = seconds
            text = f"Waiting {seconds}s..." if seconds > 0 else "Wait complete"

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
        QMessageBox.warning(None, 'Notice', message)
        self.disable_thread_controls()
        self.enable_button()
        try:
            self.statusBar().clearMessage()
        except Exception:
            pass

    def _on_qthread_finished(self):
        # ?????????????????雓飭???????????????????_finished ???????I ??????????????????
        try:
            self.disable_thread_controls()
            self.enable_button()
            try:
                self.statusBar().clearMessage()
            except Exception:
                pass
        except Exception:
            pass

    def setup_control(self):
        # self.update()
        # TODO
        self._setup_action_ui()
        self._setup_jxl_ui()
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

        # ??????????????????????????????
        # logging_mode_set expects args: (mode, _ui_pixiv_mode, _ui_fb_mode, _ui_google_mode)
        # UI object names: radioButton = pixiv, radioButton_2 = Google, radioButton_3 = FB
        # pass radioButton_3 (FB) as _ui_fb_mode and radioButton_2 (Google) as _ui_google_mode
        loggingmode = logging_mode_set(
            self.mode, self.ui.radioButton, self.ui.radioButton_3, self.ui.radioButton_2)
        self.mode = loggingmode.load_data()
        self.path, self.download_path, self.exist_pid, self.user_path1, self.Author_list, self.ban_tag, self.must_tag = self.userdata_controller.load_data()

        # ?????????璈????????窺????
        userid, cookies, agent = cookies_set(
            self.cookies, self.Agent, self.userid, self.ui.account1, self.ui.password1, self.mode).read_cookies()
        if (cookies != 0 and cookies != "" and agent != 0 and userid != 0):
            self.cookies = cookies
            self.Agent, self.userid = agent, userid
            self.ui.output.append(f"Cookies loaded for user: {self.userid}")
            self.ui.output.moveCursor(QTextCursor.End)
        else:
            self.ui.output.append(f"No valid cookies found for user: {self.userid}")
        # ????????????
        othersettings(self.ui.hidefollow, self.ui.nogif, self.ui.notag,
                  self.ui.notime, self.ui.create_dir, self.ui.no_R18G_dir,
                  getattr(self.ui, 'single_thread_mode', None),
                  getattr(self.ui, 'pid_wait_min', None),
                  getattr(self.ui, 'pid_wait_max', None),
                  getattr(self.ui, 'pid_wait_nocookie_min', None),
                  getattr(self.ui, 'pid_wait_nocookie_max', None),
                  getattr(self.ui, 'jxl_enable', None),
                  getattr(self.ui, 'jxl_cjxl_path', None),
                  getattr(self.ui, 'jxl_delete_original', None),
                  getattr(self.ui, 'jxl_effort', None)).setinfo()
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
                self.ui.output.append("test_cookies failed: " + str(e))
            except Exception:
                pass

        self._print_backup_policy()

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
            QMessageBox.warning(None, "Warning", "No running task to pause")
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
                QMessageBox.warning(None, "Warning", "Current task does not support pause")
        except Exception as e:
            QMessageBox.warning(None, "Warning", str(e))

    @QtCore.pyqtSlot()
    def on_continue_2_clicked(self):
        if not self.thread1:
            QMessageBox.warning(None, "Warning", "No running task to resume")
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
                QMessageBox.warning(None, "Warning", "Current task does not support resume")
        except Exception as e:
            QMessageBox.warning(None, "Warning", str(e))

    @QtCore.pyqtSlot()
    def on_stop_clicked(self):
        if not self.thread1:
            QMessageBox.warning(None, "Warning", "No running task to stop")
            return
        try:
            if hasattr(self.thread1, 'stop'):
                self.thread1.stop()
                # ???????????????????????????????
                try:
                    self.disable_thread_controls()
                    self.disable_button()
                except Exception:
                    pass
            else:
                QMessageBox.warning(None, "Warning", "Current task does not support stop")
        except Exception as e:
            QMessageBox.warning(None, "Warning", str(e))

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
        error_class = e.__class__.__name__  # ????????????桀????
        detail = e.args[0]  # ???????????
        cl, exc, tb = sys.exc_info()  # ???Call Stack
        lastCallStack = traceback.extract_tb(tb)[-1]  # ???Call Stack???????????????
        fileName = lastCallStack[0]  # ???????????????
        lineNum = lastCallStack[1]  # ???????????
        funcName = lastCallStack[2]  # ???????????????
        errMsg = "File \"{}\",  in {}: [{}] ".format(
            fileName, funcName, error_class)
        return (errMsg)

    @QtCore.pyqtSlot()
    def on_remove_tag_recorder_clicked(self):
        try:
            file_name = "tag_ban_pid.txt"
            os.remove(self.path+file_name)
            QMessageBox.warning(None, "Info", "Record cleared")
        except Exception as e:
            # self.add_output((self.output_err(e)))
            error_class = e.__class__.__name__  # ????????????桀????
            QMessageBox.warning(None, "Warning", error_class)

    @QtCore.pyqtSlot()
    def on_relogging_clicked(self):
        try:
            cookies = cookies_set(self.cookies, self.Agent, self.userid,
                                  self.ui.account1, self.ui.password1, self.mode)
            self.userid, self.cookies, self.Agent = cookies.get_cookies()
        except Exception as e:
            # self.add_output((self.output_err(e)))
            error_class = e.__class__.__name__  # ????????????桀????
            QMessageBox.warning(None, "Warning", error_class)

    @QtCore.pyqtSlot()
    def on_save_cookies_clicked(self):
        try:
            raw = self.ui.cookies_input.text()
            # ?????怏??????????/??????????????????
            cookies_text = raw.strip().replace('\r', '').replace('\n', ' ').strip()
            # ????????????????????????????????????? User-Agent???????????Mozilla/ ????????
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
            # ????????????cookie ????????UI
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
            try:
                from safe_io import atomic_write_json
                # cookies.json ??????????? history/ ???
                atomic_write_json(fileName, data, backup=False)
            except Exception:
                with open(fileName, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            self.cookies = cookies_text
            if ua:
                self.Agent = ua
            self.ui.output.append("Cookies saved successfully.")
        except Exception as e:
            QMessageBox.warning(None, "Warning", str(e))

    @QtCore.pyqtSlot()
    def on_browse_jxl_cjxl_clicked(self):
        try:
            selected, _ = QFileDialog.getOpenFileName(self, "Select cjxl.exe", "", "Executable (*.exe);;All Files (*)")
            if selected:
                self.ui.jxl_cjxl_path.setText(selected)
        except Exception as e:
            QMessageBox.warning(None, "Warning", str(e))

    @QtCore.pyqtSlot()
    def on_remove_like_num_clicked(self):
        try:
            file_name = "pid_num_pid.txt"
            os.remove(self.path+file_name)
            QMessageBox.warning(None, "Info", "Record cleared")
        except Exception as e:
            # self.add_output((self.output_err(e)))
            error_class = e.__class__.__name__  # ????????????桀????
            QMessageBox.warning(None, "Warning", error_class)

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
        print('????????')
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
        start_get_following(self)

    def test(self):
        # ?????????????
        self.thread1 = pixiv_thread.test_thread()
        # ????????
        self.thread1.valueChange.connect(
            self.progress_changed)  # ???????????????????????桀???????

        # ????????
        self.thread1.start()
        self.enable_thread_controls()
        while not self.thread1.isFinished():
            QThread.msleep(1)

    @QtCore.pyqtSlot()
    def on_get_pid_clicked(self):
        start_get_pid(self)

    @QtCore.pyqtSlot()
    def on_get_url_clicked(self):
        start_get_url(self)

    @QtCore.pyqtSlot()
    def on_download_url_clicked(self):
        start_download(self)

    @QtCore.pyqtSlot()
    def on_all_start_clicked(self):
        start_all(self)

    def the_next(self, num):
        continue_all(self, num)

    def timechanged(self, mytime):
        dt = QDateTime.fromString(mytime, "yyyy-MM-dd hh:mm:ss")
        if dt.isValid():
            self.ui.download_time.setDateTime(dt)
            self.last_download_time = mytime
        else:
            # ????????????????????????????????????????????????????????豲?????????????
            self.ui.download_time.setDateTime(QDateTime.currentDateTime())
            self.last_download_time = self.ui.download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss")

        # ?????????????????豲???????????????????????????????????????????????????怏????????????????????
        try:
            if getattr(self, 'userdata_controller', None):
                self.userdata_controller.write_data()
        except Exception as e:
            try:
                self.ui.output.append("Failed to persist download time: " + str(e))
            except Exception:
                pass

    def progress_changed(self, step, setmax):
        self.ui.progressBar.setMaximum(setmax)
        value = self.ui.progressBar.value() + step
        if value > setmax:
            value = setmax
        self.ui.progressBar.setValue(value)
        try:
            self.ui.progressBar.setTextVisible(True)
            self.ui.progressBar.setFormat(f"Progress {value}/{setmax}")
        except Exception:
            pass
        self.ui.progressBar.update()
        try:
            msg = f"Progress {value}/{setmax}"
            self.statusBar().showMessage(msg)
            try:
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


