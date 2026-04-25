from PyQt5.QtWidgets import QFileDialog
from PyQt5.QtCore import QDateTime
import os
import json
import pixiv_api
try:
    import update_selenium
except Exception:
    update_selenium = None
from app.core.pixiv_thread_utils import (
    normalize_cookie_entries,
    normalize_cookie_pool,
    load_exist_pid_set,
)
from app.core.settings_store import SettingsStore


class Userdata_controller:
    def __init__(self, path, exist_pid, Author_list, download_path, ban_tag, must_tag,
                 like_num, last_download_time, ui_like_num, ui_user_path1,
                 ui_download_time, ui_ban_tag_list, ui_must_tag_list,
                 ui_r18_like_num=None, ui_rule_tag_1=None, ui_rule_like_1=None,
                 ui_rule_tag_2=None, ui_rule_like_2=None):
        self.path = path
        self.exist_pid = exist_pid
        self.Author_list = Author_list
        self.download_path = download_path
        self.last_download_time = last_download_time
        self.ban_tag = ban_tag
        self.must_tag = must_tag
        self.like_num = like_num
        self._ui_user_path1 = ui_user_path1
        self._ui_ban_tag_list = ui_ban_tag_list
        self._ui_must_tag_list = ui_must_tag_list
        self._ui_like_num = ui_like_num
        self._ui_download_time = ui_download_time
        self._ui_r18_like_num = ui_r18_like_num
        self._ui_rule_tag_1 = ui_rule_tag_1
        self._ui_rule_like_1 = ui_rule_like_1
        self._ui_rule_tag_2 = ui_rule_tag_2
        self._ui_rule_like_2 = ui_rule_like_2

    def load_data(self):
        self.path = os.getenv('APPDATA') + r'/pixiv_download/'
        if not os.path.exists(self.path):
            os.mkdir(self.path)

        # exist_pid — use unified loader (handles migration from exist.json / txt)
        self.exist_pid = load_exist_pid_set(self.path)

        # following artists list
        following_json = self.path + r"/following.json"
        following_txt = self.path + r"/following.txt"
        if not os.path.exists(following_txt):
            if os.path.exists(following_json):
                try:
                    with open(following_json, encoding="utf-8") as f:
                        data = json.load(f)
                    self.Author_list = [str(item) for item in data] if isinstance(data, list) else []
                except Exception as err:
                    print(f"讀取 following.json 失敗：{err}")
                    self.Author_list = []
            else:
                print("找不到following文件")
        else:
            with open(following_txt) as f:
                self.Author_list = [line.rstrip() for line in f]

        # settings — via SettingsStore (auto-migrates legacy files)
        store = SettingsStore(self.path)
        store.migrate_from_legacy()
        d = store.get_section("download")
        try:
            self.download_path = d.get("path", "") or os.path.expanduser("~/Pixiv_download/")
            self._ui_user_path1.setText(self.download_path)
            self.ban_tag = [t for t in d.get("ban_tag", []) if not str(t).isspace()]
            self.must_tag = d.get("must_tag", [])
            self.last_download_time = d.get("download_time", "")
            self.like_num = d.get("like_num", 0)
            self._ui_like_num.setValue(int(self.like_num))
            if self._ui_r18_like_num is not None:
                try:
                    self._ui_r18_like_num.setValue(int(d.get("r18_like_num", 0)))
                except Exception:
                    self._ui_r18_like_num.setValue(0)
            if self._ui_rule_tag_1 is not None:
                try:
                    self._ui_rule_tag_1.setText(str(d.get("rule_tag_1", "")).strip())
                except Exception:
                    self._ui_rule_tag_1.setText("")
            if self._ui_rule_like_1 is not None:
                try:
                    self._ui_rule_like_1.setValue(int(d.get("rule_like_1", 0)))
                except Exception:
                    self._ui_rule_like_1.setValue(0)
            if self._ui_rule_tag_2 is not None:
                try:
                    self._ui_rule_tag_2.setText(str(d.get("rule_tag_2", "")).strip())
                except Exception:
                    self._ui_rule_tag_2.setText("")
            if self._ui_rule_like_2 is not None:
                try:
                    self._ui_rule_like_2.setValue(int(d.get("rule_like_2", 0)))
                except Exception:
                    self._ui_rule_like_2.setValue(0)
            if self.last_download_time == "":
                self._ui_download_time.setDateTime(QDateTime.currentDateTime())
            else:
                self._ui_download_time.setDateTime(
                    QDateTime.fromString(self.last_download_time, "yyyy-MM-dd hh:mm:ss"))
            self._ui_ban_tag_list.addItems(self.ban_tag)
            self._ui_must_tag_list.addItems(self.must_tag)
        except Exception as err:
            print(err)
            self.download_path = os.path.expanduser("~/Pixiv_download/")
            self._ui_user_path1.setText(self.download_path)
            self._ui_download_time.setDateTime(QDateTime.currentDateTime())
            self.write_data()

        return (self.path, self.download_path, self.exist_pid,
                self._ui_user_path1, self.Author_list, self.ban_tag, self.must_tag)

    def open_folder(self):
        folder_path = QFileDialog.getExistingDirectory(None, "Open folder", "./")
        return folder_path

    def set_downloaa_path(self):
        self.download_path = self.open_folder() + '/'
        self._ui_user_path1.setText(self.download_path)

    def write_data(self):
        def _spin(w):
            try:
                return int(w.value()) if w is not None else 0
            except Exception:
                return 0

        def _text(w):
            try:
                return str(w.text()).strip() if w is not None else ""
            except Exception:
                return ""

        store = SettingsStore(self.path)
        store.update_section("download", {
            "path": _text(self._ui_user_path1),
            "like_num": _spin(self._ui_like_num),
            "r18_like_num": _spin(self._ui_r18_like_num),
            "ban_tag": self.ban_tag,
            "must_tag": self.must_tag,
            "download_time": self._ui_download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss"),
            "rule_tag_1": _text(self._ui_rule_tag_1),
            "rule_like_1": _spin(self._ui_rule_like_1),
            "rule_tag_2": _text(self._ui_rule_tag_2),
            "rule_like_2": _spin(self._ui_rule_like_2),
        })


class logging_mode_set:
    def __init__(self, mode, _ui_pixiv_mode, _ui_fb_mode, _ui_google_mode):
        self.mode = mode
        self._ui_pixiv_mode = _ui_pixiv_mode
        self._ui_fb_mode = _ui_fb_mode
        self._ui_google_mode = _ui_google_mode
        self.path = os.getenv('APPDATA') + r'/pixiv_download/'

    def load_data(self):
        store = SettingsStore(self.path)
        store.migrate_from_legacy()
        self.mode = store.get_section("auth").get("login_mode", 0)
        if self.mode == 0:
            self._ui_pixiv_mode.setChecked(True)
        elif self.mode == 1:
            self._ui_google_mode.setChecked(True)
        else:
            self._ui_fb_mode.setChecked(True)
        return self.mode

    def write_data(self):
        if self._ui_pixiv_mode.isChecked():
            self.mode = 0
        elif self._ui_google_mode.isChecked():
            self.mode = 1
        else:
            self.mode = 2
        store = SettingsStore(self.path)
        store.update_fields("auth", {"login_mode": self.mode})


class othersettings:
    def __init__(self, hidefollow, nogif, notag, notime, create_dir, no_R18G_dir,
                 ai_gen_dir=None, single_thread_mode=None,
                 pid_wait_min=None, pid_wait_max=None,
                 pid_wait_nocookie_min=None, pid_wait_nocookie_max=None,
                 jxl_enable=None, jxl_cjxl_path=None,
                 jxl_delete_original=None, jxl_effort=None):
        self._ui_hidefollow = hidefollow
        self._ui_nogif = nogif
        self._ui_notag = notag
        self._ui_notime = notime
        self._ui_create_dir = create_dir
        self._ui_no_R18G_dir = no_R18G_dir
        self._ui_ai_gen_dir = ai_gen_dir
        self._ui_single_thread_mode = single_thread_mode
        self._ui_pid_wait_min = pid_wait_min
        self._ui_pid_wait_max = pid_wait_max
        self._ui_pid_wait_nocookie_min = pid_wait_nocookie_min
        self._ui_pid_wait_nocookie_max = pid_wait_nocookie_max
        self._ui_jxl_enable = jxl_enable
        self._ui_jxl_cjxl_path = jxl_cjxl_path
        self._ui_jxl_delete_original = jxl_delete_original
        self._ui_jxl_effort = jxl_effort
        self.path = os.getenv('APPDATA') + r'/pixiv_download/'

    def setinfo(self):
        try:
            store = SettingsStore(self.path)
            store.migrate_from_legacy()
            f = store.get_section("filter")
            d = store.get_section("directory")
            p = store.get_section("performance")
            j = store.get_section("jxl")

            self._ui_hidefollow.setChecked(f.get("hidefollow", False))
            self._ui_nogif.setChecked(f.get("nogif", False))
            self._ui_notag.setChecked(f.get("notag", False))
            self._ui_notime.setChecked(f.get("notime", False))
            self._ui_create_dir.setChecked(d.get("create_dir", False))
            self._ui_no_R18G_dir.setChecked(d.get("no_R18G_dir", False))
            if self._ui_ai_gen_dir is not None:
                try:
                    self._ui_ai_gen_dir.setChecked(bool(d.get("ai_gen_dir", False)))
                except Exception:
                    pass
            if self._ui_single_thread_mode is not None:
                try:
                    self._ui_single_thread_mode.setChecked(bool(p.get("single_thread_mode", False)))
                except Exception:
                    pass
            if self._ui_pid_wait_min is not None:
                try:
                    self._ui_pid_wait_min.setValue(int(p.get("pid_wait_min", 10)))
                except Exception:
                    pass
            if self._ui_pid_wait_max is not None:
                try:
                    self._ui_pid_wait_max.setValue(int(p.get("pid_wait_max", 60)))
                except Exception:
                    pass
            if self._ui_pid_wait_nocookie_min is not None:
                try:
                    self._ui_pid_wait_nocookie_min.setValue(int(p.get("pid_wait_nocookie_min", 1)))
                except Exception:
                    pass
            if self._ui_pid_wait_nocookie_max is not None:
                try:
                    self._ui_pid_wait_nocookie_max.setValue(int(p.get("pid_wait_nocookie_max", 6)))
                except Exception:
                    pass
            if self._ui_jxl_enable is not None:
                try:
                    self._ui_jxl_enable.setChecked(bool(j.get("enable", False)))
                except Exception:
                    pass
            if self._ui_jxl_cjxl_path is not None:
                try:
                    self._ui_jxl_cjxl_path.setText(str(j.get("cjxl_path", "")))
                except Exception:
                    pass
            if self._ui_jxl_delete_original is not None:
                try:
                    self._ui_jxl_delete_original.setChecked(bool(j.get("delete_original", False)))
                except Exception:
                    pass
            if self._ui_jxl_effort is not None:
                try:
                    effort = max(1, min(9, int(j.get("effort", 7))))
                    self._ui_jxl_effort.setValue(effort)
                except Exception:
                    pass
        except Exception:
            pass

    def write_other_date(self):
        def _chk(w, default=False):
            try:
                return bool(w.isChecked()) if w is not None else default
            except Exception:
                return default

        def _spin(w, default=0):
            try:
                return int(w.value()) if w is not None else default
            except Exception:
                return default

        def _text(w, default=""):
            try:
                return str(w.text()).strip() if w is not None else default
            except Exception:
                return default

        effort = _spin(self._ui_jxl_effort, 7)
        effort = max(1, min(9, effort))

        store = SettingsStore(self.path)
        store.update_multiple({
            "filter": {
                **store.get_section("filter"),
                "hidefollow": _chk(self._ui_hidefollow),
                "nogif": _chk(self._ui_nogif),
                "notag": _chk(self._ui_notag),
                "notime": _chk(self._ui_notime),
            },
            "directory": {
                "create_dir": _chk(self._ui_create_dir),
                "no_R18G_dir": _chk(self._ui_no_R18G_dir),
                "ai_gen_dir": _chk(self._ui_ai_gen_dir),
            },
            "performance": {
                "single_thread_mode": _chk(self._ui_single_thread_mode),
                "pid_wait_min": _spin(self._ui_pid_wait_min, 10),
                "pid_wait_max": _spin(self._ui_pid_wait_max, 60),
                "pid_wait_nocookie_min": _spin(self._ui_pid_wait_nocookie_min, 1),
                "pid_wait_nocookie_max": _spin(self._ui_pid_wait_nocookie_max, 6),
            },
            "jxl": {
                "enable": _chk(self._ui_jxl_enable),
                "cjxl_path": _text(self._ui_jxl_cjxl_path),
                "delete_original": _chk(self._ui_jxl_delete_original),
                "effort": effort,
            },
        })


class cookies_set:
    def __init__(self, cookies, Agent, userid, _ui_anncount_input, _ui_password_input, mode=None):
        self.mode = mode
        self.path = os.getenv('APPDATA') + r'/pixiv_download/'
        self.cookies = cookies
        self.cookies_pool = []
        self.Agent = Agent
        self.userid = userid
        self._ui_anncount_input = _ui_anncount_input
        self._ui_password_input = _ui_password_input

    def _normalize_cookie_pool(self, raw_value):
        return normalize_cookie_pool(raw_value)

    def _normalize_cookie_entries(self, raw_entries, alias_map=None):
        return normalize_cookie_entries(raw_entries, alias_map=alias_map)

    def read_cookies(self):
        try:
            store = SettingsStore(self.path)
            store.migrate_from_legacy()
            data = store.get_section("auth")
            self.Agent = data.get("agent", self.Agent)
            alias_map = data.get("cookies_aliases", {})
            if not isinstance(alias_map, dict):
                alias_map = {}
            entries = self._normalize_cookie_entries(data.get("cookies_entries", []), alias_map=alias_map)
            if not entries:
                pool = self._normalize_cookie_pool(data.get("cookies_pool", []))
                if not pool:
                    pool = self._normalize_cookie_pool(data.get("cookies", ""))
                entries = self._normalize_cookie_entries(pool, alias_map=alias_map)
            pool = [x.get("cookie", "") for x in entries if str(x.get("cookie", "")).strip()]
            self.cookies_pool = pool
            self.cookies = pool[0] if pool else ""
            self.userid = data.get("userid", self.userid)
            self.account = data.get("account", "")
            self.password = data.get("password", "")
            self._ui_anncount_input.setText(self.account)
            self._ui_password_input.setText(self.password)
            return self.userid, self.cookies, self.Agent, self.cookies_pool, entries
        except Exception:
            print("read cookies failed")
            return 0, 0, 0, [], []

    def write_cookies(self):
        alias_map = {}
        entries = self._normalize_cookie_entries(self.cookies)
        if entries:
            pool = [x.get("cookie", "") for x in entries if str(x.get("cookie", "")).strip()]
            alias_map = {
                x.get("cookie", ""): str(x.get("alias", "") or "").strip()
                for x in entries if str(x.get("cookie", "")).strip()
            }
        else:
            pool = self._normalize_cookie_pool(self.cookies)
            entries = self._normalize_cookie_entries(pool)
            alias_map = {
                x.get("cookie", ""): str(x.get("alias", "") or "").strip()
                for x in entries if str(x.get("cookie", "")).strip()
            }
        self.cookies_pool = pool
        self.cookies = pool[0] if pool else ""
        store = SettingsStore(self.path)
        # Preserve login_mode when writing auth section
        current_auth = store.get_section("auth")
        store.update_section("auth", {
            **current_auth,
            "agent": self.Agent,
            "userid": self.userid,
            "account": self._ui_anncount_input.text(),
            "password": self._ui_password_input.text(),
            "cookies": self.cookies,
            "cookies_pool": pool,
            "cookies_aliases": alias_map,
            "cookies_entries": entries,
        })

    def get_cookies(self):
        anncount = self._ui_anncount_input.text()
        password = self._ui_password_input.text()
        if update_selenium is None:
            raise RuntimeError("selenium / update_selenium not available")
        update_selenium.update()
        return pixiv_api.auto_get_cookie(anncount, password, mode=self.mode)


class userpass:
    def __init__(self, _ui_pass_tag, _ui_pass_num):
        self.path = os.getenv('APPDATA') + r'/pixiv_download/'
        self._ui_pass_tag = _ui_pass_tag
        self._ui_pass_num = _ui_pass_num

    def read_info(self):
        try:
            store = SettingsStore(self.path)
            store.migrate_from_legacy()
            f = store.get_section("filter")
            self._ui_pass_tag.setChecked(bool(f.get("pass_tag", False)))
            self._ui_pass_num.setChecked(bool(f.get("pass_num", False)))
            return 1
        except Exception:
            return 0

    def write(self):
        try:
            store = SettingsStore(self.path)
            store.update_fields("filter", {
                "pass_tag": self._ui_pass_tag.isChecked(),
                "pass_num": self._ui_pass_num.isChecked(),
            })
            return 1
        except Exception:
            return 0
