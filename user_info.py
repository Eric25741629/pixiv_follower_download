from PyQt5.QtWidgets import (QApplication, QMessageBox)
from PyQt5 import QtCore
import os
import json
from PyQt5.QtWidgets import QFileDialog
from PyQt5.QtCore import QDateTime
import pixiv_api
import update_selenium


class Userdata_controller(object):
    def __init__(self, path, exist_pid, Author_list, download_path, ban_tag, must_tag, like_num, last_download_time, ui_like_num, ui_user_path1, ui_download_time, ui_ban_tag_list, ui_must_tag_list):

        self.path = path
        self.exist_pid = exist_pid
        self.Author_list = Author_list
        self.download_path = download_path
        self.last_download_time = last_download_time
        self.ban_tag = ban_tag
        self.must_tag = must_tag
        self.like_num = like_num
        '''ui'''
        self._ui_user_path1 = ui_user_path1
        self._ui_ban_tag_list = ui_ban_tag_list
        self._ui_must_tag_list = ui_must_tag_list
        self._ui_like_num = ui_like_num
        self._ui_download_time = ui_download_time

    def load_data(self):
        print("load")
        self.path = os.getenv('APPDATA')+r'/pixiv_download/'
        if not os.path.exists(self.path):
            os.mkdir(self.path)
        # 統一優先讀取 exist_pid.json
        json_path = self.path + r"/exist_pid.json"
        legacy_json_path = self.path + r"/exist.json"
        txt_path = self.path + r"/existPID.txt"
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.exist_pid = set(str(i).replace("p0", "") for i in data)
                else:
                    self.exist_pid = set()
            except Exception as err:
                print(f"讀取 exist_pid.json 失敗：{err}")
                self.exist_pid = set()
        elif os.path.exists(legacy_json_path):
            # 相容舊檔 exist.json，並自動遷移到 exist_pid.json
            try:
                with open(legacy_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.exist_pid = set(str(i).replace("p0", "") for i in data)
                else:
                    self.exist_pid = set()
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(sorted(self.exist_pid), f, ensure_ascii=False, indent=2)
            except Exception as err:
                print(f"讀取/遷移 exist.json 失敗：{err}")
                self.exist_pid = set()
        elif os.path.exists(txt_path):
            with open(txt_path, encoding='utf-8') as file:  # 相容舊格式
                self.exist_pid = set(line.rstrip().replace("p0", "") for line in file if line.rstrip())
            # 自動遷移到 json
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(sorted(self.exist_pid), f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        else:
            self.exist_pid = set()
        # self.pixiv_pid(self.exist_pid)
        if not os.path.exists(self.path+r"/following.txt"):
            # 如果沒有 txt，那就嘗試讀 json 版本
            json_path = self.path + r"/following.json"
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as file:
                        data = json.load(file)
                        # 保證是 list 並轉成字串
                        if isinstance(data, list):
                            self.Author_list = [str(item) for item in data]
                        else:
                            self.Author_list = []
                except Exception as err:
                    print(f"讀取 following.json 失敗：{err}")
                    self.Author_list = []
            else:
                print("找不到following文件")
        else:
            with open((self.path+r"/following.txt")) as file:  # 讀取寫入的文檔
                self.Author_list = [line.rstrip() for line in file]
        if os.path.isfile(self.path+'data.json'):
            try:
                with open(self.path+'data.json') as f:
                    data = json.load(f)
                    self.download_path = data['user_download_path']
                    self._ui_user_path1.setText(self.download_path)
                    try:
                        self.ban_tag = [
                            i for i in data['ban_tag'] if not i.isspace()]
                        self.must_tag = data['must_tag']
                        self.last_download_time = data['download_time']
                        self.like_num = data['like_num']
                        self._ui_like_num.setValue(int(self.like_num))
                        if (self.last_download_time == ""):
                            self._ui_download_time.setDateTime(
                                QDateTime.currentDateTime())
                        else:
                            self._ui_download_time.setDateTime(QDateTime.fromString(
                                self.last_download_time, "yyyy-MM-dd hh:mm:ss"))
                        self._ui_ban_tag_list.addItems(self.ban_tag)
                        self._ui_must_tag_list.addItems(self.must_tag)

                    except Exception as err:
                        print(err)
                        self._ui_like_num = data
                        self._ui_download_time.setDateTime(
                            QDateTime.currentDateTime())
                        pass
            except Exception as err:
                print(err)
                print("加載user_data文件失敗")
                self.download_path = os.path.expanduser("~/Pixiv_download/")
                self._ui_user_path1.setText(self.download_path)
                self._ui_download_time.setDateTime(QDateTime.currentDateTime())
                self.write_data()

        else:
            print("找不到user_data文件")
            self.download_path = os.path.expanduser("~/Pixiv_download/")
            self.write_data()
        return self.path, self.download_path, self.exist_pid, self._ui_user_path1, self.Author_list, self.ban_tag, self.must_tag

    def open_folder(self):
        folder_path = QFileDialog.getExistingDirectory(None,
                                                       "Open folder",
                                                       "./")                 # start path
        print(folder_path)
        path = folder_path
        return path

    def set_downloaa_path(self):
        self.download_path = self.open_folder()+'/'
        self._ui_user_path1.setText(self.download_path)

    def write_data(self):
        # print(len(self.ban_tag))
        jsonObject = {
            "user_download_path": self._ui_user_path1.text(),
            "like_num": self._ui_like_num.value(),
            "ban_tag": self.ban_tag,
            "must_tag": self.must_tag,
            "download_time": self._ui_download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss")
        }
        user_data = self.path
        fileName = user_data+"data.json"
        file = open(fileName, "w")
        json.dump(jsonObject, file, indent=4)
        file.close()


class logging_mode_set(object):
    def __init__(self, mode, _ui_pixiv_mode, _ui_fb_mode, _ui_google_mode):
        self.mode = mode
        self._ui_pixiv_mode, self._ui_fb_mode, self._ui_google_mode = _ui_pixiv_mode, _ui_fb_mode, _ui_google_mode
        self.path = os.getenv('APPDATA')+r'/pixiv_download/'

    def load_data(self):
        print("loading mode")
        if os.path.isfile(self.path+'logging.json'):
            try:
                with open(self.path+'logging.json') as f:
                    data = json.load(f)
                    self.mode = data['logging_mode']
                    if (self.mode == 0):
                        self._ui_pixiv_mode.setChecked(True)
                    elif (self.mode == 1):
                        self._ui_google_mode.setChecked(True)
                    else:
                        self._ui_fb_mode.setChecked(True)
                return self.mode
            except Exception as err:
                print(err)
                print("加載logging文件失敗")

    def write_data(self):
        if self._ui_pixiv_mode.isChecked() == True:
            self.mode = 0
        elif self._ui_google_mode.isChecked() == True:
            self.mode = 1
        else:
            self.mode = 2
        jsonObject = {
            "logging_mode": self.mode
        }
        user_data = self.path
        fileName = user_data+"logging.json"
        file = open(fileName, "w")
        json.dump(jsonObject, file, indent=4)
        file.close()


class othersettings(object):
    def __init__(self, hidefollow, nogif, notag, notime, create_dir, no_R18G_dir, single_thread_mode=None, pid_wait_min=None, pid_wait_max=None):
        self._ui_hidefollow = hidefollow
        self._ui_nogif = nogif
        self._ui_notag = notag
        self._ui_notime = notime
        self._ui_create_dir = create_dir
        self._ui_no_R18G_dir = no_R18G_dir
        self._ui_single_thread_mode = single_thread_mode
        self._ui_pid_wait_min = pid_wait_min
        self._ui_pid_wait_max = pid_wait_max
        self.path = os.getenv('APPDATA')+r'/pixiv_download/'

    def setinfo(self):
        try:
            with open(self.path+r'/othersettings.json') as f:
                data = json.load(f)
                self._ui_hidefollow.setChecked(data['hidefollow'])
                self._ui_nogif.setChecked(data['nogif'])
                self._ui_notag.setChecked(data['notag'])
                self._ui_notime.setChecked(data['notime'])
                self._ui_create_dir.setChecked(data['create_dir'])
                self._ui_no_R18G_dir.setChecked(data['no_R18G_dir'])
                if self._ui_single_thread_mode is not None:
                    try:
                        # default to False if key missing
                        self._ui_single_thread_mode.setChecked(bool(data.get('single_thread_mode', False)))
                    except Exception:
                        pass
                if self._ui_pid_wait_min is not None:
                    try:
                        self._ui_pid_wait_min.setValue(int(data.get('pid_wait_min', 10)))
                    except Exception:
                        pass
                if self._ui_pid_wait_max is not None:
                    try:
                        self._ui_pid_wait_max.setValue(int(data.get('pid_wait_max', 60)))
                    except Exception:
                        pass
        except:
            pass

    def write_other_date(self):
        fileName = self.path+"/othersettings.json"
        jsonObject = {
            "hidefollow": self._ui_hidefollow.isChecked(),
            "nogif": self._ui_nogif.isChecked(),
            "notag": self._ui_notag.isChecked(),
            "notime": self._ui_notime.isChecked(),
            "create_dir": self._ui_create_dir.isChecked(),
            "no_R18G_dir": self._ui_no_R18G_dir.isChecked()
        }
        # include single_thread_mode if UI provided
        if self._ui_single_thread_mode is not None:
            try:
                jsonObject['single_thread_mode'] = bool(self._ui_single_thread_mode.isChecked())
            except Exception:
                jsonObject['single_thread_mode'] = False
        if self._ui_pid_wait_min is not None:
            try:
                jsonObject['pid_wait_min'] = int(self._ui_pid_wait_min.value())
            except Exception:
                jsonObject['pid_wait_min'] = 10
        if self._ui_pid_wait_max is not None:
            try:
                jsonObject['pid_wait_max'] = int(self._ui_pid_wait_max.value())
            except Exception:
                jsonObject['pid_wait_max'] = 60
        file = open(fileName, "w")
        json.dump(jsonObject, file, indent=4)
        file.close()


class cookies_set(object):
    def __init__(self, cookies, Agent, userid, _ui_anncount_input, _ui_password_input, mode=None):
        self.mode = mode
        self.path = os.getenv('APPDATA')+r'/pixiv_download/'
        self.cookies = cookies
        self.Agent = Agent
        self.userid = userid
        self._ui_anncount_input = _ui_anncount_input
        self._ui_password_input = _ui_password_input

    def read_cookies(self):  # 讀取寫入的cookies
        # print(len(lines))
        try:
            with open(self.path+r'/cookies.json') as f:
                data = json.load(f)
                self.Agent = data['agent']
                self.cookies = data['cookies']
                self.userid = data['userid']
                self.account = data['account']
                self.password = data['password']
                self._ui_anncount_input.setText(self.account)
                self._ui_password_input.setText(self.password)
            return self.userid, self.cookies, self.Agent
        except:
            print('加載cookies失敗')
            return 0, 0, 0

    def write_cookies(self):  # 寫入獲得的cookies
        user_data = self.path
        fileName = user_data+"/cookies.json"
        jsonObject = {"agent": self.Agent, "userid": self.userid, "account": self._ui_anncount_input.text(
        ), "password": self._ui_password_input.text(), "cookies": self.cookies}
        file = open(fileName, "w")
        json.dump(jsonObject, file, indent=4)
        file.close()

    def get_cookies(self):
        anncount = self._ui_anncount_input.text()
        password = self._ui_password_input.text()
        update_selenium.update()
        return (pixiv_api.auto_get_cookie(anncount, password, mode=self.mode))

    '''def get_cookies(self):
        with open((self.path+r"/password.txt")) as file:     #讀取寫入的文檔
            texts = [line.rstrip() for line in file]
        for i in range(0,len(texts),2):
            self.userid,self.cookies,self.Agent=pixiv_api.auto_get_cookie(texts[i],texts[i+1],mode=self.mode)'''
    '''def get_cookies(self):
        with open((self.path+r"/password.txt")) as file:     #讀取寫入的文檔
            texts = [line.rstrip() for line in file]
        for i in range(0,1,2):
            self.userid,self.cookies,self.Agent=pixiv_api.auto_get_cookie(texts[i],texts[i+1],mode=self.mode)'''


class userpass(object):
    def __init__(self, _ui_pass_tag, _ui_pass_num):
        self.path = os.getenv('APPDATA')+r'/pixiv_download/'
        self._ui_pass_tag = _ui_pass_tag
        self._ui_pass_num = _ui_pass_num

    def read_info(self):  # 讀取寫入的cookies
        try:
            with open(self.path+r'/pass.json') as f:
                data = json.load(f)
                self._ui_pass_tag.setChecked(data['pass_tag'])
                self._ui_pass_num.setChecked(data['pass_num'])
            return 1
        except:
            # print('加載userpass失敗')
            return 0

    def write(self):
        try:
            user_data = self.path
            fileName = user_data+"/pass.json"
            jsonObject = {"pass_tag": self._ui_pass_tag.isChecked(
            ), "pass_num": self._ui_pass_num.isChecked()}
            file = open(fileName, "w")
            json.dump(jsonObject, file, indent=4)
            file.close()
            return 1
        except:
            # print('寫入userpass失敗')
            return 0
