
try:
    import gdown
except Exception:
    gdown = None
from user_info import *
from app.core import pixiv_thread
from app.core.pixiv_thread_utils import normalize_cookie_pool
from PyQt5.QtCore import QThread, pyqtSignal
from datetime import datetime
import os
import json
import re
import subprocess
import time
from queue import Queue

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from PyQt5.QtWidgets import QFileDialog
from PyQt5 import uic
try:
    from qframelesswindow import FramelessMainWindow
    HAS_FRAMELESS_WINDOW = True
except Exception:
    FramelessMainWindow = QtWidgets.QMainWindow
    HAS_FRAMELESS_WINDOW = False

from pathlib import Path
import sys
import traceback
import pixiv_api
from app.gui.run_actions import (
    start_get_following,
    start_get_pid,
    start_get_url,
    start_download,
    start_all,
    continue_all,
    _find_default_cjxl_path,
)
global cookies
global Agent
global path


class Runthread(QtCore.QThread):
    # 測試用背景執行緒：定期回報進度
    _signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()

    def run(self):
        for i in range(100):
            time.sleep(0.2)
            self._signal.emit(int(i))  # 透過 pyqtSignal 將進度值送回 UI


class MainWindow_controller(FramelessMainWindow):
    path = 'none'
    download_path = None
    exist_pid = ''
    Author_list = []
    cookies = ""
    cookies_pool = []
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

    @staticmethod
    def _brief_text(value, limit=120):
        try:
            text = str(value)
        except Exception:
            return "<unavailable>"
        text = text.replace("\r", " ").replace("\n", " ")
        if len(text) > limit:
            return text[:limit] + f"... (len={len(text)})"
        return text

    def _normalize_cookie_pool(self, raw_value):
        return normalize_cookie_pool(raw_value)

    def _set_cookie_pool(self, pool):
        normalized = self._normalize_cookie_pool(pool)
        old_status = getattr(self, "_cookie_status_map", {}) if isinstance(getattr(self, "_cookie_status_map", {}), dict) else {}
        old_alias = getattr(self, "_cookie_alias_map", {}) if isinstance(getattr(self, "_cookie_alias_map", {}), dict) else {}
        self._cookie_status_map = {c: old_status.get(c, "未知") for c in normalized}
        self._cookie_alias_map = {c: str(old_alias.get(c, "") or "").strip() for c in normalized}
        self.cookies_pool = normalized
        self.cookies = normalized[0] if normalized else ""
        try:
            self._refresh_cookie_list_ui()
        except Exception:
            pass

    def _set_cookie_entries(self, entries):
        normalized = []
        seen = set()
        if not isinstance(entries, list):
            entries = []
        for e in entries:
            cookie_text = ""
            alias_text = ""
            if isinstance(e, dict):
                cookie_text = str(e.get("cookie", "") or "").strip()
                alias_text = str(e.get("alias", "") or "").strip()
            else:
                cookie_text = str(e or "").strip()
            if not cookie_text:
                continue
            if cookie_text.lower().startswith("cookie:"):
                cookie_text = cookie_text.split(":", 1)[1].strip()
            if not cookie_text or cookie_text in seen:
                continue
            seen.add(cookie_text)
            normalized.append({"cookie": cookie_text, "alias": alias_text})
        old_status = getattr(self, "_cookie_status_map", {}) if isinstance(getattr(self, "_cookie_status_map", {}), dict) else {}
        self.cookies_pool = [x["cookie"] for x in normalized]
        self.cookies = self.cookies_pool[0] if self.cookies_pool else ""
        self._cookie_alias_map = {x["cookie"]: x["alias"] for x in normalized}
        self._cookie_status_map = {c: old_status.get(c, "未知") for c in self.cookies_pool}
        try:
            self._refresh_cookie_list_ui()
        except Exception:
            pass

    def _get_cookie_entries(self):
        pool = self._get_cookie_pool()
        alias_map = getattr(self, "_cookie_alias_map", {})
        out = []
        for cookie in pool:
            alias = ""
            if isinstance(alias_map, dict):
                alias = str(alias_map.get(cookie, "") or "").strip()
            out.append({"cookie": cookie, "alias": alias})
        return out

    def _alias_label(self, cookie_text, idx=None):
        alias_map = getattr(self, "_cookie_alias_map", {})
        alias = ""
        if isinstance(alias_map, dict):
            alias = str(alias_map.get(cookie_text, "") or "").strip()
        if alias:
            return alias
        if idx is not None:
            return f"Cookie{idx}"
        return "未命名"

    def _get_cookie_pool(self):
        pool = self._normalize_cookie_pool(getattr(self, "cookies_pool", []))
        if not pool:
            pool = self._normalize_cookie_pool(self.cookies)
        return pool

    def _parse_cookie_input(self, raw_text):
        raw = str(raw_text or "")
        text = raw.strip()
        if not text:
            return [], None

        ua = None
        ua_line_match = re.search(r"User-Agent:\s*([^\r\n]+)$", text, flags=re.IGNORECASE)
        if ua_line_match:
            ua = ua_line_match.group(1).strip()
            text = text[:ua_line_match.start()].strip()
        ua_match = re.search(r"(Mozilla/[^\r\n]+)$", text)
        if ua_match:
            ua = ua_match.group(1).strip()
            text = text[:ua_match.start()].strip().rstrip(";").strip()

        json_pool = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                json_pool = [str(x or "").strip() for x in parsed]
        except Exception:
            json_pool = None

        if json_pool is not None:
            pool = self._normalize_cookie_pool(json_pool)
            return pool, ua

        normalized_text = text.replace("\r", "\n")
        for sep in ("|||", "||", "\n"):
            normalized_text = normalized_text.replace(sep, "\n")
        pool = self._normalize_cookie_pool(normalized_text.split("\n"))
        if not pool and text:
            pool = self._normalize_cookie_pool(text)
        return pool, ua

    def _cookie_preview(self, cookie_text, limit=88):
        text = str(cookie_text or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return text[:limit] + f"... (len={len(text)})"

    def _setup_cookie_list_ui(self):
        try:
            if not hasattr(self.ui, "cookieAliasInput") or self.ui.cookieAliasInput is None:
                self.ui.cookieAliasInput = QLineEdit(self.ui.groupBox_cookies)
                self.ui.cookieAliasInput.setObjectName("cookieAliasInput")
                self.ui.cookieAliasInput.setPlaceholderText("別名（例：主帳號）")
                self.ui.cookieAliasInput.setMaximumWidth(180)
                # cookies_input | alias_input | save_btn
                self.ui.horizontalLayout_cookies.insertWidget(1, self.ui.cookieAliasInput)
        except Exception:
            pass

        layout = getattr(self.ui, "verticalLayout_cookie_input", None)
        if layout is None:
            return
        if hasattr(self.ui, "cookies_list_widget") and self.ui.cookies_list_widget is not None:
            return

        self.ui.cookies_list_label = QLabel("已儲存 Cookies 列表", self.ui.groupBox_cookies)
        self.ui.cookies_list_label.setObjectName("cookies_list_label")
        layout.addWidget(self.ui.cookies_list_label)

        row = QHBoxLayout()
        row.setObjectName("horizontalLayout_cookie_list_row")
        self.ui.cookies_list_widget = QListWidget(self.ui.groupBox_cookies)
        self.ui.cookies_list_widget.setObjectName("cookies_list_widget")
        self.ui.cookies_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.ui.cookies_list_widget.setMinimumHeight(80)
        row.addWidget(self.ui.cookies_list_widget, 1)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        self.ui.removeCookieBtn = QPushButton("移除單筆", self.ui.groupBox_cookies)
        self.ui.removeCookieBtn.setObjectName("removeCookieBtn")
        self.ui.removeCookieBtn.setMinimumWidth(96)
        right_col.addWidget(self.ui.removeCookieBtn)

        self.ui.testCookiesStatusBtn = QPushButton("測試失效", self.ui.groupBox_cookies)
        self.ui.testCookiesStatusBtn.setObjectName("testCookiesStatusBtn")
        self.ui.testCookiesStatusBtn.setMinimumWidth(96)
        right_col.addWidget(self.ui.testCookiesStatusBtn)
        right_col.addStretch(1)
        row.addLayout(right_col, 0)
        layout.addLayout(row)

        self.ui.removeCookieBtn.clicked.connect(self.on_remove_cookie_clicked)
        self.ui.testCookiesStatusBtn.clicked.connect(self.on_test_cookie_status_clicked)
        self.ui.cookies_list_widget.itemDoubleClicked.connect(self.on_cookie_item_double_clicked)

    def _refresh_cookie_list_ui(self):
        widget = getattr(self.ui, "cookies_list_widget", None)
        if widget is None:
            return
        widget.clear()
        pool = self._get_cookie_pool()
        status_map = getattr(self, "_cookie_status_map", {})
        for i, cookie in enumerate(pool, start=1):
            status = "未知"
            if isinstance(status_map, dict):
                status = str(status_map.get(cookie, "未知"))
            alias = self._alias_label(cookie, idx=i)
            if status == "有效":
                marker = "OK"
            elif status == "失效":
                marker = "失效"
            else:
                marker = "未測"
            item = QListWidgetItem(f"[{i}][{marker}][{alias}] {self._cookie_preview(cookie)}")
            item.setData(Qt.UserRole, cookie)
            item.setData(Qt.UserRole + 1, alias)
            widget.addItem(item)

    def _persist_cookies_to_disk(self, agent_override=None):
        user_data_path = os.getenv('APPDATA') + r'/pixiv_download/'
        if not os.path.exists(user_data_path):
            os.makedirs(user_data_path, exist_ok=True)
        fileName = os.path.join(user_data_path, 'cookies.json')
        entries = self._get_cookie_entries()
        pool = [e.get("cookie", "") for e in entries if str(e.get("cookie", "")).strip()]
        aliases = {e.get("cookie", ""): str(e.get("alias", "") or "").strip() for e in entries if str(e.get("cookie", "")).strip()}
        if agent_override:
            self.Agent = agent_override
        data = {
            "agent": self.Agent,
            "userid": self.userid or "",
            "account": self.ui.account1.text(),
            "password": self.ui.password1.text(),
            "cookies": (pool[0] if pool else ""),
            "cookies_pool": pool,
            "cookies_aliases": aliases,
            "cookies_entries": entries,
        }
        try:
            from safe_io import atomic_write_json
            atomic_write_json(fileName, data, backup=False)
        except Exception:
            with open(fileName, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_quick_data_root(self):
        return os.path.join(os.getenv('APPDATA') or "", "pixiv_download")

    def _get_quick_download_root(self):
        try:
            p = str(self.ui.user_path1.text()).strip()
        except Exception:
            p = ""
        if not p:
            p = str(getattr(self, "download_path", "") or "").strip()
        return p

    def _open_path(self, target, prefer_parent_when_missing=True):
        path = str(target or "").strip()
        if not path:
            QMessageBox.warning(None, "警告", "路徑為空，無法開啟。")
            return
        path = os.path.normpath(path)
        try:
            if os.path.isdir(path) or os.path.isfile(path):
                os.startfile(path)
                return
            if prefer_parent_when_missing:
                parent = os.path.dirname(path)
                if parent and os.path.isdir(parent):
                    try:
                        subprocess.Popen(["explorer", "/select,", path])
                    except Exception:
                        os.startfile(parent)
                    self.add_output(f"檔案不存在，已開啟所在資料夾：{parent}")
                    return
            QMessageBox.warning(None, "警告", f"找不到路徑：{path}")
        except Exception as e:
            QMessageBox.warning(None, "警告", str(e))

    def _setup_quick_open_tab(self):
        try:
            if hasattr(self.ui, "settings_tab_quick_open") and self.ui.settings_tab_quick_open is not None:
                return

            tab = QWidget()
            tab.setObjectName("settings_tab_quick_open")
            root_layout = QVBoxLayout(tab)
            root_layout.setSpacing(8)

            title = QLabel("快速開啟常用設定與資料夾")
            title.setWordWrap(True)
            root_layout.addWidget(title)

            grid = QGridLayout()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            root_layout.addLayout(grid)

            root = self._get_quick_data_root()
            entries = [
                ("開啟設定根目錄", lambda: self._open_path(root, prefer_parent_when_missing=False)),
                ("開啟下載資料夾", lambda: self._open_path(self._get_quick_download_root(), prefer_parent_when_missing=False)),
                ("開啟 cookies.json", lambda: self._open_path(os.path.join(root, "cookies.json"))),
                ("開啟 othersettings.json", lambda: self._open_path(os.path.join(root, "othersettings.json"))),
                ("開啟 userdata.json", lambda: self._open_path(os.path.join(root, "userdata.json"))),
                ("開啟 pass.json", lambda: self._open_path(os.path.join(root, "pass.json"))),
                ("開啟 exist_pid.json", lambda: self._open_path(os.path.join(root, "exist_pid.json"))),
                ("開啟 following.txt", lambda: self._open_path(os.path.join(root, "following.txt"))),
                ("開啟 all_url.txt", lambda: self._open_path(os.path.join(root, "all_url.txt"))),
                ("開啟 all_url_meta.json", lambda: self._open_path(os.path.join(root, "all_url_meta.json"))),
                ("開啟 author_progress.json", lambda: self._open_path(os.path.join(root, "author_progress.json"))),
                ("開啟 cookie需求紀錄", lambda: self._open_path(os.path.join(root, "pixiv_cookie_requirement.json"))),
            ]

            for idx, (text, handler) in enumerate(entries):
                row = idx // 2
                col = idx % 2
                btn = QPushButton(text, tab)
                btn.setMinimumHeight(34)
                btn.clicked.connect(handler)
                grid.addWidget(btn, row, col)

            hint = QLabel("提示：若檔案不存在，會自動開啟所在資料夾，方便你直接建立或貼上資料。")
            hint.setWordWrap(True)
            root_layout.addWidget(hint)
            root_layout.addStretch(1)

            self.ui.settings_tab_quick_open = tab
            self.ui.settings_tabs.addTab(tab, "快速開啟")
        except Exception:
            pass

    def __init__(self):
        super().__init__()  # in python3, super(Class, self).xxx = super().xxx
        self.cookies_pool = []
        self._cookie_alias_map = {}
        self._cookie_status_map = {}
        ui_path = Path(__file__).resolve().parents[2] / "test.ui"
        uic.loadUi(str(ui_path), self)
        self.ui = self
        self._setup_cookie_list_ui()
        self._setup_quick_open_tab()
        self._setup_window_controls()
        self._drag_active = False
        self._drag_offset = QPoint()
        self._apply_window_visual_style()
        try:
            # 限制輸出區保留行數，避免長時間執行造成記憶體持續增長
            self.ui.output.document().setMaximumBlockCount(2000)
        except Exception:
            pass
        self.thread1 = None  # 目前正在執行的工作執行緒
        self._countdown_output_active = False
        self._countdown_block_number = None
        self._last_countdown_second = None
        self._last_progress_log_value = 0
        self._last_progress_log_max = 0
        self._countdown_log_to_output = True
        self._output_autoscroll_enabled = True
        self._output_programmatic_scroll = False
        self._output_user_scroll_intent = False
        self._output_scroll_notice_ms = 1400
        self._init_output_scroll_behavior()

    def _init_output_scroll_behavior(self):
        try:
            output = self.ui.output
            if output is None:
                return
            sb = output.verticalScrollBar()
            if sb is not None:
                try:
                    sb.valueChanged.connect(self._on_output_scroll_value_changed)
                except Exception:
                    pass
                try:
                    sb.sliderPressed.connect(self._on_output_slider_pressed)
                except Exception:
                    pass
                try:
                    sb.sliderReleased.connect(self._on_output_slider_released)
                except Exception:
                    pass
                try:
                    sb.installEventFilter(self)
                except Exception:
                    pass
            try:
                output.installEventFilter(self)
            except Exception:
                pass
            try:
                output.viewport().installEventFilter(self)
            except Exception:
                pass
        except Exception:
            pass

    def eventFilter(self, obj, event):
        try:
            output = getattr(self.ui, "output", None)
            if output is not None:
                watched = [output]
                try:
                    watched.append(output.viewport())
                except Exception:
                    pass
                try:
                    watched.append(output.verticalScrollBar())
                except Exception:
                    pass
                if obj in watched:
                    t = event.type()
                    if t in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.MouseMove):
                        self._output_user_scroll_intent = True
                    elif t == QEvent.KeyPress:
                        try:
                            key = event.key()
                        except Exception:
                            key = None
                        if key in (
                            Qt.Key_Up,
                            Qt.Key_Down,
                            Qt.Key_PageUp,
                            Qt.Key_PageDown,
                            Qt.Key_Home,
                            Qt.Key_End,
                        ):
                            self._output_user_scroll_intent = True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _on_output_slider_pressed(self):
        self._output_user_scroll_intent = True

    def _on_output_slider_released(self):
        try:
            if self._is_output_at_bottom() and not self._output_autoscroll_enabled:
                self._set_output_autoscroll(True, show_tip=True)
        except Exception:
            pass

    def _is_output_at_bottom(self, margin=2):
        try:
            sb = self.ui.output.verticalScrollBar()
            if sb is None:
                return True
            return int(sb.value()) >= int(sb.maximum()) - int(margin)
        except Exception:
            return True

    def _show_autoscroll_tip(self, enabled):
        try:
            tip = "自動滾動：開啟" if enabled else "自動滾動：已暫停（捲到最底會自動恢復）"
            p = self.ui.output.mapToGlobal(QPoint(max(8, self.ui.output.width() - 260), max(8, self.ui.output.height() - 28)))
            QToolTip.showText(p, tip, self.ui.output, self.ui.output.rect(), int(self._output_scroll_notice_ms))
        except Exception:
            pass

    def _set_output_autoscroll(self, enabled, show_tip=False):
        enabled = bool(enabled)
        if bool(getattr(self, "_output_autoscroll_enabled", True)) == enabled:
            return
        self._output_autoscroll_enabled = enabled
        if show_tip:
            self._show_autoscroll_tip(enabled)

    def _on_output_scroll_value_changed(self, _value):
        try:
            if bool(getattr(self, "_output_programmatic_scroll", False)):
                return
            at_bottom = self._is_output_at_bottom()
            if bool(getattr(self, "_output_user_scroll_intent", False)):
                if self._output_autoscroll_enabled and (not at_bottom):
                    self._set_output_autoscroll(False, show_tip=True)
                elif (not self._output_autoscroll_enabled) and at_bottom:
                    self._set_output_autoscroll(True, show_tip=True)
                self._output_user_scroll_intent = False
                return
            if (not self._output_autoscroll_enabled) and at_bottom:
                self._set_output_autoscroll(True, show_tip=True)
        except Exception:
            pass

    def _append_output_line(self, text):
        try:
            output = self.ui.output
            sb = output.verticalScrollBar()
            keep = sb.value() if sb is not None else 0
            self._output_programmatic_scroll = True
            output.append(text)
            if self._output_autoscroll_enabled:
                if sb is not None:
                    sb.setValue(sb.maximum())
            else:
                if sb is not None:
                    sb.setValue(keep)
        except Exception:
            try:
                self.ui.output.append(text)
            except Exception:
                pass
        finally:
            self._output_programmatic_scroll = False

    def reset_progress_state(self):
        try:
            self._last_progress_log_value = 0
            self._last_progress_log_max = 0
            self._countdown_output_active = False
            self._countdown_block_number = None
            self._last_countdown_second = None
            self.ui.progressBar.reset()
            self.ui.progressBar.setValue(0)
            max_value = int(self.ui.progressBar.maximum())
            if max_value > 0:
                self.ui.progressBar.setFormat(f"進度 0/{max_value}")
        except Exception:
            pass

    def _setup_window_controls(self):
        try:
            top_bar = QHBoxLayout()
            top_bar.setContentsMargins(0, 0, 0, 0)
            top_bar.setSpacing(6)
            top_bar.addStretch(1)

            self.window_min_btn = QPushButton("—", self.ui.centralwidget)
            self.window_min_btn.setObjectName("window_min_btn")
            self.window_min_btn.setFixedSize(34, 28)
            self.window_min_btn.clicked.connect(self.showMinimized)
            top_bar.addWidget(self.window_min_btn)

            self.window_max_btn = QPushButton("□", self.ui.centralwidget)
            self.window_max_btn.setObjectName("window_max_btn")
            self.window_max_btn.setFixedSize(34, 28)
            self.window_max_btn.clicked.connect(self._toggle_max_restore)
            top_bar.addWidget(self.window_max_btn)

            self.window_close_btn = QPushButton("×", self.ui.centralwidget)
            self.window_close_btn.setObjectName("window_close_btn")
            self.window_close_btn.setFixedSize(34, 28)
            self.window_close_btn.clicked.connect(self.close)
            top_bar.addWidget(self.window_close_btn)

            self.ui.verticalLayout_4.insertLayout(0, top_bar)
        except Exception:
            pass

    def _toggle_max_restore(self):
        try:
            if self.isMaximized():
                self.showNormal()
                self.window_max_btn.setText("□")
            else:
                self.showMaximized()
                self.window_max_btn.setText("❐")
        except Exception:
            pass

    def _apply_window_visual_style(self):
        # Frameless window by third-party library; fallback to Qt flag if unavailable.
        if not HAS_FRAMELESS_WINDOW:
            try:
                self.setWindowFlag(Qt.FramelessWindowHint, True)
            except Exception:
                pass
        # Phase 35: WA_TranslucentBackground caused QProgressBar / QTabBar /
        # QSplitter::handle to render with no background, hiding their text
        # against the desktop. qframelesswindow handles the rounded shape
        # itself so we no longer need this attribute.
        try:
            self.setWindowOpacity(0.995)
        except Exception:
            pass
        try:
            self.ui.centralwidget.setAttribute(Qt.WA_StyledBackground, True)
            self.ui.centralwidget.setObjectName("centralwidget")
        except Exception:
            pass
        try:
            shadow = QGraphicsDropShadowEffect(self.ui.centralwidget)
            shadow.setBlurRadius(36)
            shadow.setOffset(0, 8)
            shadow.setColor(QColor(0, 0, 0, 70))
            self.ui.centralwidget.setGraphicsEffect(shadow)
        except Exception:
            pass
        try:
            self.setStyleSheet(
                """
                QMainWindow { background: transparent; }
                QWidget { color: #1f2937; }
                #centralwidget {
                    background: rgba(248, 251, 255, 245);
                    border: 1px solid rgba(255, 255, 255, 170);
                    border-radius: 10px;
                }
                QTabWidget::pane, QTextBrowser, QListWidget, QLineEdit, QSpinBox, QDateTimeEdit {
                    background: rgba(255, 255, 255, 245);
                    border: 1px solid rgba(120, 136, 165, 180);
                    border-radius: 8px;
                }
                QTabBar::tab {
                    background: rgba(248, 251, 255, 235);
                    border: 1px solid rgba(120, 136, 165, 160);
                    padding: 4px 12px;
                    color: #1f2937;
                }
                QTabBar::tab:selected {
                    background: rgba(255, 255, 255, 250);
                    border-bottom: 1px solid rgba(255, 255, 255, 250);
                }
                QSplitter::handle {
                    background: rgba(248, 251, 255, 230);
                }
                QStatusBar {
                    background: rgba(248, 251, 255, 235);
                    color: #1f2937;
                }
                QProgressBar {
                    background: rgba(255, 255, 255, 245);
                    border: 1px solid rgba(120, 136, 165, 180);
                    border-radius: 6px;
                    text-align: center;
                    color: #1f2937;
                }
                QProgressBar::chunk {
                    background: rgba(74, 123, 255, 200);
                    border-radius: 5px;
                }
                QGroupBox {
                    background: rgba(255, 255, 255, 245);
                    border: 1px solid rgba(120, 136, 165, 180);
                    border-radius: 8px;
                    margin-top: 12px;
                    padding-top: 10px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    subcontrol-position: top left;
                    left: 10px;
                    padding: 0 6px;
                    background: rgba(248, 251, 255, 245);
                    color: #1f2937;
                }
                QLabel {
                    background: transparent;
                }
                QRadioButton, QCheckBox {
                    spacing: 6px;
                }
                QLineEdit, QSpinBox, QDateTimeEdit, QListWidget, QTextBrowser {
                    border: 2px solid rgba(106, 126, 168, 200);
                    padding: 4px 6px;
                    selection-background-color: rgba(74, 123, 255, 95);
                }
                QLineEdit:focus, QSpinBox:focus, QDateTimeEdit:focus, QListWidget:focus, QTextBrowser:focus {
                    border: 2px solid rgba(74, 123, 255, 230);
                    background: rgba(255, 255, 255, 235);
                }
                QPushButton {
                    background: rgba(248, 251, 255, 235);
                    border: 2px solid rgba(106, 126, 168, 210);
                    border-radius: 6px;
                    padding: 4px 8px;
                    color: #1f2937;
                }
                QPushButton:hover {
                    background: rgba(236, 244, 255, 245);
                    border: 2px solid rgba(74, 123, 255, 220);
                }
                QPushButton:pressed {
                    background: rgba(221, 233, 255, 245);
                    border: 2px solid rgba(63, 104, 219, 225);
                }
                #window_min_btn, #window_max_btn, #window_close_btn {
                    background: rgba(255, 255, 255, 195);
                    border: 1px solid rgba(180, 190, 210, 150);
                    border-radius: 6px;
                    padding: 0;
                    font-size: 14px;
                }
                #window_close_btn:hover {
                    background: rgba(220, 68, 55, 220);
                    color: white;
                    border: 1px solid rgba(220, 68, 55, 220);
                }
                """
            )
        except Exception:
            pass

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() <= 56:
            self._drag_active = True
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_active = False
        super().mouseReleaseEvent(event)

    def update(self):
        if gdown is None:
            try:
                self.ui.output.append("已略過更新：缺少可選依賴 gdown。")
            except Exception:
                pass
            return
        output = os.getenv('APPDATA')+r'/pixiv_download/update.txt'
        url = 'https://drive.google.com/u/1/uc?id=1uGppiPKA6TF0Zxz3SjCnAp_5_0YsPXYF&export=download'
        gdown.download(url, output)
        with open(output, encoding="utf-8") as file:  # 讀取更新資訊
            data = json.load(file)

        msgBox = QMessageBox()
        msgBox.setTextFormat(Qt.RichText)
        msgBox.setText(
            "偵測到新版本，請到這裡下載：<a href='https://drive.google.com/file/d/1zZF9kFVCP8HuwbZLu6sqEiyQmaEbuUSJ/view?usp=share_link'>點我開啟下載頁</a>。")
        msgBox.setWindowTitle("版本更新提醒")
        msgBox.exec_()
        '''output=self.path+r'/check.json'

        with open(output, encoding="utf-8") as file:     # 讀取更新資訊
            data = json.load(file)
        vision=float(data['updata_vision'])
        if vision>0.15:
            self.label.setOpenExternalLinks(True)
            self.label.setText(u'<a href="https://datutu.blog.csdn.net/" style="color:#0000ff;"><b> 前往 CSDN 查看更新公告</b></a>')'''

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
            self._append_output_line(out_str)
            self._scroll_output_to_end()
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
                                                  self.ui.must_tag_list,
                                                  self.ui.r18_like_num,
                                                  self.ui.rule_tag_1,
                                                  self.ui.rule_like_1,
                                                  self.ui.rule_tag_2,
                                                  self.ui.rule_like_2,
                                                  )
        try:
            userdata_controller.write_data()
        except Exception as e:
            try:
                self.ui.output.append("Failed to save user data: " + str(e))
            except Exception:
                pass
        try:
            if self.thread1 is not None and self.thread1.isRunning():
                self.thread1.stop()
                if not self.thread1.wait(5000):
                    self.thread1.terminate()
                    self.thread1.wait(2000)
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
            cookies_set(self._get_cookie_entries(), self.Agent, self.userid,
                        self.ui.account1, self.ui.password1, self.mode).write_cookies()
        except Exception as e:
            try:
                self.ui.output.append("Failed to save cookies: " + str(e))
            except Exception:
                pass

        try:
            othersettings(self.ui.hidefollow, self.ui.nogif, self.ui.notag, self.ui.notime,
                          self.ui.create_dir, self.ui.no_R18G_dir,
                          getattr(self.ui, 'ai_gen_dir', None),
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
        try:
            cookie_len = len(self._get_cookie_pool())
        except Exception:
            cookie_len = 0
        print(f"[controller] ui_cookies: current mode={self.mode}, cookie_len={cookie_len}")
        if not self._get_cookie_pool():
            if self.ui.account1.text() == '' or self.ui.password1.text() == '':
                QMessageBox.warning(None, "警告", "請輸入帳號密碼，或貼上 Cookies。")
                return
            cookies = cookies_set(
                self.cookies, self.Agent, self.userid, self.ui.account1, self.ui.password1, self.mode
            )
            self.userid, self.cookies, self.Agent = cookies.get_cookies()
            self._set_cookie_pool([self.cookies])

    def log_start(self, text):
        try:
            self.add_output('開始：' + text)
            print('[controller] ' + text + ' started')
        except Exception:
            try:
                print('[controller] ' + text + ' 啟動（UI 輸出寫入失敗）')
            except Exception:
                pass

    def _setup_action_ui(self):
        try:
            self.ui.get_following.setText('步驟 1：抓取追蹤畫師')
            self.ui.get_pid.setText('步驟 2：抓取作品 PID')
            self.ui.get_url.setText('步驟 3：抓取作品資訊')
            self.ui.download_url.setText('步驟 4：開始下載')
            self.ui.all_start.setText('全部執行（1→4）')
            self.ui.get_following.setToolTip('先同步追蹤畫師清單')
            self.ui.get_pid.setToolTip('從追蹤畫師抓取作品 PID')
            self.ui.get_url.setToolTip('依 PID 取得作品網址與資訊')
            self.ui.download_url.setToolTip('從 all_url.txt 開始下載')
            self.ui.all_start.setToolTip('依序執行步驟 1 到 4')
            for btn in (
                self.ui.get_following,
                self.ui.get_pid,
                self.ui.get_url,
                self.ui.download_url,
                self.ui.all_start,
                self.ui.pause_all,
                self.ui.continue_2,
                self.ui.stop,
            ):
                btn.setMinimumHeight(34)
        except Exception:
            pass

    def _browse_cjxl_path(self):
        try:
            start_dir = os.path.expanduser("~")
            if hasattr(self.ui, "jxl_cjxl_path"):
                candidate = os.path.dirname(self.ui.jxl_cjxl_path.text().strip())
                if candidate:
                    start_dir = candidate
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "選擇 cjxl.exe",
                start_dir,
                "可執行檔 (cjxl.exe);;所有檔案 (*)",
            )
            if selected and hasattr(self.ui, "jxl_cjxl_path"):
                self.ui.jxl_cjxl_path.setText(selected)
        except Exception:
            pass

    def _ensure_jxl_controls(self):
        # Prefer fixed UI controls from test.ui; fall back to dynamic injection for legacy UIs.
        if hasattr(self.ui, "jxl_enable") and hasattr(self.ui, "jxl_cjxl_path") and hasattr(self.ui, "jxl_effort"):
            try:
                if not self.ui.jxl_cjxl_path.text().strip():
                    self.ui.jxl_cjxl_path.setText(_find_default_cjxl_path())
            except Exception:
                pass
            try:
                if hasattr(self.ui, "jxl_browse_btn"):
                    self.ui.jxl_browse_btn.clicked.connect(self._browse_cjxl_path)
            except Exception:
                pass
            return
        try:
            panel = QGroupBox("JXL 設定", self.ui.run_tab_actions)
            panel.setObjectName("jxl_settings_panel")
            row = QHBoxLayout(panel)
            row.setContentsMargins(8, 8, 8, 8)
            row.setSpacing(8)

            self.ui.jxl_enable = QCheckBox("下載後轉為 JXL", panel)
            self.ui.jxl_enable.setChecked(False)
            row.addWidget(self.ui.jxl_enable)

            row.addWidget(QLabel("cjxl：", panel))
            self.ui.jxl_cjxl_path = QLineEdit(panel)
            self.ui.jxl_cjxl_path.setPlaceholderText("cjxl.exe 路徑")
            try:
                self.ui.jxl_cjxl_path.setText(_find_default_cjxl_path())
            except Exception:
                pass
            row.addWidget(self.ui.jxl_cjxl_path, 1)

            browse_btn = QPushButton("瀏覽", panel)
            browse_btn.clicked.connect(self._browse_cjxl_path)
            row.addWidget(browse_btn)

            self.ui.jxl_delete_original = QCheckBox("成功後刪除原圖", panel)
            self.ui.jxl_delete_original.setChecked(False)
            row.addWidget(self.ui.jxl_delete_original)

            row.addWidget(QLabel("壓縮強度：", panel))
            self.ui.jxl_effort = QSpinBox(panel)
            self.ui.jxl_effort.setRange(1, 9)
            self.ui.jxl_effort.setValue(7)
            row.addWidget(self.ui.jxl_effort)

            self.ui.run_tab_actions_layout.insertWidget(1, panel)
        except Exception:
            pass

    def _print_backup_policy(self):
        try:
            self.ui.output.append('[備份策略]')
            self.ui.output.append('1) atomic_write_* 會把舊檔備份到同層 history/')
            self.ui.output.append('2) backup=False 會略過 history（例如 cookies.json）')
            self.ui.output.append('3) 命名：filename.YYYYMMDD(.N)，最多保留 10 份')
        except Exception:
            pass

    def _get_countdown_pid_suffix(self):
        try:
            thread = getattr(self, "thread1", None)
            pid = str(getattr(thread, "_countdown_pid", "") or "").strip()
            if pid:
                return f" (PID {pid})"
        except Exception:
            pass
        return ""

    @QtCore.pyqtSlot(int)
    def update_countdown(self, seconds):
        pid_suffix = self._get_countdown_pid_suffix()
        try:
            self.statusBar().showMessage(f"等待 {seconds} 秒...{pid_suffix}" if seconds > 0 else f"等待完成{pid_suffix}")
        except Exception:
            pass
        if not bool(getattr(self, "_countdown_log_to_output", False)):
            try:
                if seconds <= 0:
                    self._countdown_output_active = False
                    self._countdown_block_number = None
                    self._last_countdown_second = None
            except Exception:
                pass
            return
        try:
            if self._last_countdown_second == seconds:
                return
            self._last_countdown_second = seconds
            text = f"等待 {seconds} 秒...{pid_suffix}" if seconds > 0 else f"等待完成{pid_suffix}"

            if not self._countdown_output_active:
                self._append_output_line(text)
                self._countdown_output_active = True
            else:
                sb = self.ui.output.verticalScrollBar()
                keep = sb.value() if sb is not None else 0
                self._output_programmatic_scroll = True
                cursor = self.ui.output.textCursor()
                cursor.movePosition(QTextCursor.End)
                cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                cursor.insertText(text)
                self.ui.output.setTextCursor(cursor)
                if sb is not None and (not self._output_autoscroll_enabled):
                    sb.setValue(keep)
                self._output_programmatic_scroll = False

            if seconds <= 0:
                self._countdown_output_active = False
                self._countdown_block_number = None
                self._last_countdown_second = None
            self._scroll_output_to_end()
        except Exception:
            pass

    def _scroll_output_to_end(self):
        try:
            if not bool(getattr(self, "_output_autoscroll_enabled", True)):
                return
            output_widget = self.ui.output

            def _do_scroll():
                try:
                    self._output_programmatic_scroll = True
                    output_widget.moveCursor(QTextCursor.End)
                    try:
                        sb = output_widget.verticalScrollBar()
                        if sb is not None:
                            sb.setValue(sb.maximum())
                    except Exception:
                        pass
                    self._output_programmatic_scroll = False
                except Exception:
                    pass

            QtCore.QTimer.singleShot(0, _do_scroll)
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
        QMessageBox.warning(None, '通知', message)
        self.disable_thread_controls()
        self.enable_button()
        try:
            self.statusBar().clearMessage()
        except Exception:
            pass

    def _on_qthread_finished(self):
        # 子執行緒結束後統一還原 UI 狀態，避免按鈕卡在禁用
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
        self._ensure_jxl_controls()
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
                                                       self.ui.must_tag_list,
                                                       self.ui.r18_like_num,
                                                       self.ui.rule_tag_1,
                                                       self.ui.rule_like_1,
                                                       self.ui.rule_tag_2,
                                                       self.ui.rule_like_2,
                                                       )

        # 初始化登入模式（Pixiv/FB/Google）
        # logging_mode_set expects args: (mode, _ui_pixiv_mode, _ui_fb_mode, _ui_google_mode)
        # UI object names: radioButton = pixiv, radioButton_2 = Google, radioButton_3 = FB
        # pass radioButton_3 (FB) as _ui_fb_mode and radioButton_2 (Google) as _ui_google_mode
        loggingmode = logging_mode_set(
            self.mode, self.ui.radioButton, self.ui.radioButton_3, self.ui.radioButton_2)
        self.mode = loggingmode.load_data()
        self.path, self.download_path, self.exist_pid, self.user_path1, self.Author_list, self.ban_tag, self.must_tag = self.userdata_controller.load_data()

        # 載入 cookies 與使用者資訊
        userid, cookies, agent, cookie_pool, cookie_entries = cookies_set(
            self.cookies, self.Agent, self.userid, self.ui.account1, self.ui.password1, self.mode).read_cookies()
        if (agent != 0 and agent != ""):
            self.Agent = agent
        if userid != 0:
            self.userid = userid
        if cookie_entries:
            self._set_cookie_entries(cookie_entries)
            cookie_pool = self._get_cookie_pool()
            try:
                if hasattr(self.ui, "cookieAliasInput") and self.ui.cookieAliasInput is not None:
                    first = cookie_pool[0] if cookie_pool else ""
                    self.ui.cookieAliasInput.setText(str(self._cookie_alias_map.get(first, "") or ""))
            except Exception:
                pass
            user_preview = self._brief_text(self.userid)
            self.add_output(f"已載入 Cookies（{len(cookie_pool)} 組），使用者：{user_preview}")
            try:
                self.ui.cookies_input.setText("")
            except Exception:
                pass
        elif cookie_pool:
            self._set_cookie_pool(cookie_pool)
            user_preview = self._brief_text(self.userid)
            self.add_output(f"已載入 Cookies（{len(cookie_pool)} 組），使用者：{user_preview}")
            try:
                self.ui.cookies_input.setText("")
            except Exception:
                pass
        else:
            self._set_cookie_pool([])
            user_preview = self._brief_text(self.userid)
            self.add_output(f"找不到有效 Cookies，使用者：{user_preview}")
        # 載入其他設定（隱藏關注、跳過 GIF、下載規則等）
        othersettings(self.ui.hidefollow, self.ui.nogif, self.ui.notag,
                  self.ui.notime, self.ui.create_dir, self.ui.no_R18G_dir,
                  getattr(self.ui, 'ai_gen_dir', None),
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
                self.ui.output.append("test_cookies 失敗：" + str(e))
            except Exception:
                pass

        self._print_backup_policy()

    def test_cookies(self):
        pool = self._get_cookie_pool()
        if not pool:
            return
        test, cookies = pixiv_api.Test_cookies(pool, self.Agent)
        print(test)
        valid_set = set(self._normalize_cookie_pool(cookies))
        self._cookie_status_map = {
            c: ("有效" if c in valid_set else "失效") for c in pool
        }
        self._refresh_cookie_list_ui()
        try:
            self.ui.output.append(f"Cookies 啟動檢查：有效 {len(valid_set)} / 全部 {len(pool)}")
            invalid = [c for c in pool if c not in valid_set]
            for idx, cookie in enumerate(invalid[:5], start=1):
                self.ui.output.append(f"[啟動失效 {idx}] {self._alias_label(cookie)}")
        except Exception:
            pass
        if test == 0:
            cookies = cookies_set(self.cookies, self.Agent, self.userid,
                                  self.ui.account1, self.ui.password1, self.mode)
            # self.userid, self.cookies, self.Agent = cookies.get_cookies()

    @QtCore.pyqtSlot()
    def on_pause_all_clicked(self):
        if not self.thread1:
            QMessageBox.warning(None, "警告", "目前沒有可暫停的任務")
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
                QMessageBox.warning(None, "警告", "目前任務不支援暫停")
        except Exception as e:
            QMessageBox.warning(None, "警告", str(e))

    @QtCore.pyqtSlot()
    def on_continue_2_clicked(self):
        if not self.thread1:
            QMessageBox.warning(None, "警告", "目前沒有可繼續的任務")
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
                QMessageBox.warning(None, "警告", "目前任務不支援繼續")
        except Exception as e:
            QMessageBox.warning(None, "警告", str(e))

    @QtCore.pyqtSlot()
    def on_stop_clicked(self):
        if not self.thread1:
            QMessageBox.warning(None, "警告", "目前沒有可停止的任務")
            return
        try:
            if hasattr(self.thread1, 'stop'):
                self.thread1.stop()
                # 發出停止後先鎖 UI，等待執行緒結束再恢復
                try:
                    self.disable_thread_controls()
                    self.disable_button()
                except Exception:
                    pass
            else:
                QMessageBox.warning(None, "警告", "目前任務不支援停止")
        except Exception as e:
            QMessageBox.warning(None, "警告", str(e))

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
        error_class = e.__class__.__name__  # 錯誤類型
        detail = e.args[0]  # 錯誤詳細內容
        cl, exc, tb = sys.exc_info()  # 取得 call stack
        lastCallStack = traceback.extract_tb(tb)[-1]  # 最後一層 call stack
        fileName = lastCallStack[0]  # 發生錯誤的檔案
        lineNum = lastCallStack[1]  # 發生錯誤的行號
        funcName = lastCallStack[2]  # 發生錯誤的函式
        errMsg = f"File \"{fileName}\",  in {funcName}: [{error_class}] "
        return (errMsg)

    @QtCore.pyqtSlot()
    def on_remove_tag_recorder_clicked(self):
        try:
            file_name = "tag_ban_pid.txt"
            os.remove(self.path+file_name)
            QMessageBox.warning(None, "資訊", "記錄已清除")
        except Exception as e:
            # self.add_output((self.output_err(e)))
            error_class = e.__class__.__name__  # 錯誤類型
            QMessageBox.warning(None, "警告", error_class)

    @QtCore.pyqtSlot()
    def on_relogging_clicked(self):
        try:
            cookies = cookies_set(self.cookies, self.Agent, self.userid,
                                  self.ui.account1, self.ui.password1, self.mode)
            self.userid, self.cookies, self.Agent = cookies.get_cookies()
            self._set_cookie_pool([self.cookies])
        except Exception as e:
            # self.add_output((self.output_err(e)))
            error_class = e.__class__.__name__  # 錯誤類型
            QMessageBox.warning(None, "警告", error_class)

    @QtCore.pyqtSlot()
    def on_save_cookies_clicked(self):
        try:
            raw = self.ui.cookies_input.text()
            input_pool, ua = self._parse_cookie_input(raw)
            alias_input = getattr(self.ui, "cookieAliasInput", None)
            alias_text = str(alias_input.text()).strip() if alias_input is not None else ""

            pool = self._get_cookie_pool()
            alias_map = dict(getattr(self, "_cookie_alias_map", {}) or {})

            # 支援只修改已選取項目的別名（不輸入 cookie 內容）
            if not input_pool:
                widget = getattr(self.ui, "cookies_list_widget", None)
                row = widget.currentRow() if widget is not None else -1
                if row >= 0 and row < len(pool) and alias_text:
                    target_cookie = pool[row]
                    alias_map[target_cookie] = alias_text
                    self._cookie_alias_map = alias_map
                    self._refresh_cookie_list_ui()
                    self._persist_cookies_to_disk(agent_override=ua)
                    self.ui.output.append(f"已更新別名：{self._alias_label(target_cookie)}")
                    return
                QMessageBox.warning(None, "警告", "Cookies 內容為空，請輸入至少一組，或先選取列表項目再填別名。")
                return

            before = len(pool)
            for idx, cookie in enumerate(input_pool):
                if cookie not in pool:
                    pool.append(cookie)
                if idx == 0 and alias_text:
                    alias_map[cookie] = alias_text
                elif cookie not in alias_map:
                    alias_map[cookie] = ""

            self._set_cookie_pool(pool)
            self._cookie_alias_map = {c: str(alias_map.get(c, "") or "").strip() for c in pool}
            self._refresh_cookie_list_ui()
            self._persist_cookies_to_disk(agent_override=ua)
            self.ui.cookies_input.clear()
            if alias_input is not None:
                alias_input.clear()
            added = len(pool) - before
            if added > 0:
                self.ui.output.append(f"已新增 {added} 組 Cookies，列表目前共 {len(pool)} 組。")
            else:
                self.ui.output.append("這筆 Cookies 已存在，已更新別名（若有填寫）。")
        except Exception as e:
            QMessageBox.warning(None, "警告", str(e))

    @QtCore.pyqtSlot()
    def on_remove_cookie_clicked(self):
        try:
            widget = getattr(self.ui, "cookies_list_widget", None)
            if widget is None:
                return
            row = widget.currentRow()
            if row < 0:
                QMessageBox.warning(None, "警告", "請先選取要移除的 Cookies。")
                return
            pool = self._get_cookie_pool()
            if row >= len(pool):
                return
            target_cookie = pool[row]
            del pool[row]
            self._set_cookie_pool(pool)
            try:
                if isinstance(self._cookie_alias_map, dict):
                    self._cookie_alias_map.pop(target_cookie, None)
                if isinstance(self._cookie_status_map, dict):
                    self._cookie_status_map.pop(target_cookie, None)
            except Exception:
                pass
            self._persist_cookies_to_disk()
            self.ui.output.append(f"已移除 1 組 Cookies，剩餘 {len(pool)} 組。")
        except Exception as e:
            QMessageBox.warning(None, "警告", str(e))

    @QtCore.pyqtSlot()
    def on_test_cookie_status_clicked(self):
        try:
            pool = self._get_cookie_pool()
            if not pool:
                QMessageBox.warning(None, "警告", "目前沒有 Cookies 可測試。")
                return
            self.ui.output.append(f"開始測試 Cookies，共 {len(pool)} 組...")
            QApplication.processEvents()
            ok_count, valid_list = pixiv_api.Test_cookies(pool, self.Agent)
            valid_set = set(self._normalize_cookie_pool(valid_list))
            self._cookie_status_map = {
                c: ("有效" if c in valid_set else "失效") for c in pool
            }
            self._refresh_cookie_list_ui()
            invalid = [c for c in pool if c not in valid_set]
            self.ui.output.append(f"測試完成：有效 {len(valid_set)}，失效 {len(invalid)}")
            for idx, cookie in enumerate(invalid[:10], start=1):
                self.ui.output.append(f"[失效 {idx}] {self._alias_label(cookie)} | {self._cookie_preview(cookie, limit=56)}")
            if len(invalid) > 10:
                self.ui.output.append(f"... 其餘失效 {len(invalid)-10} 組未展開")
            try:
                self._persist_cookies_to_disk()
            except Exception:
                pass
            QMessageBox.information(
                None,
                "Cookies 測試完成",
                f"測試完成：有效 {len(valid_set)}，失效 {len(invalid)}",
            )
        except Exception as e:
            QMessageBox.warning(None, "警告", str(e))

    @QtCore.pyqtSlot(QListWidgetItem)
    def on_cookie_item_double_clicked(self, item):
        try:
            cookie_text = str(item.data(Qt.UserRole) or "").strip()
            if cookie_text:
                self.ui.cookies_input.setText(cookie_text)
            alias_text = str(item.data(Qt.UserRole + 1) or "").strip()
            alias_input = getattr(self.ui, "cookieAliasInput", None)
            if alias_input is not None:
                alias_input.setText(alias_text)
        except Exception:
            pass

    @QtCore.pyqtSlot()
    def on_remove_like_num_clicked(self):
        try:
            file_name = "pid_num_pid.txt"
            os.remove(self.path+file_name)
            QMessageBox.warning(None, "資訊", "記錄已清除")
        except Exception as e:
            # self.add_output((self.output_err(e)))
            error_class = e.__class__.__name__  # 錯誤類型
            QMessageBox.warning(None, "警告", error_class)

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
        print('切換為 Pixiv 模式')
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
        # 建立測試執行緒
        self.thread1 = pixiv_thread.test_thread()
        # 連接進度訊號
        self.thread1.valueChange.connect(
            self.progress_changed)  # 收到進度後更新 UI 進度條

        # 啟動執行緒
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
            # 若時間字串格式不合法，回退為目前系統時間
            self.ui.download_time.setDateTime(QDateTime.currentDateTime())
            self.last_download_time = self.ui.download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss")

        # 時間變更後立即嘗試寫回設定，避免重啟後遺失
        try:
            if getattr(self, 'userdata_controller', None):
                self.userdata_controller.write_data()
        except Exception as e:
            try:
                self.ui.output.append("Failed to persist download time: " + str(e))
            except Exception:
                pass

    def progress_changed(self, step, setmax):
        try:
            if self.ui.progressBar.maximum() != int(setmax):
                self.ui.progressBar.setMaximum(int(setmax))
                self.ui.progressBar.setValue(0)
                self._last_progress_log_value = 0
                self._last_progress_log_max = int(setmax)
        except Exception:
            self.ui.progressBar.setMaximum(setmax)
        value = self.ui.progressBar.value() + step
        if value > setmax:
            value = setmax
        if value < 0:
            value = 0
        self.ui.progressBar.setValue(value)
        try:
            self.ui.progressBar.setTextVisible(True)
            self.ui.progressBar.setFormat(f"進度 {value}/{setmax}")
        except Exception:
            pass
        self.ui.progressBar.update()
        try:
            msg = f"進度 {value}/{setmax}"
            self.statusBar().showMessage(msg)
            # 大量 PID 時避免每步都寫入 output，否則 UI 會明顯卡頓。
            try:
                threshold = 20
                if int(setmax) >= 5000:
                    threshold = 500
                elif int(setmax) >= 1000:
                    threshold = 100
                elif int(setmax) >= 200:
                    threshold = 50
                if int(setmax) <= 200:
                    need_log = (value == setmax) or (value <= 5) or ((value - int(self._last_progress_log_value or 0)) >= threshold)
                else:
                    need_log = (value == setmax) or ((value - int(self._last_progress_log_value or 0)) >= threshold)
                if need_log:
                    self._last_progress_log_value = int(value)
                    self._last_progress_log_max = int(setmax)
                    self._append_output_line(msg)
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




