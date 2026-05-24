from __future__ import annotations
import math
import os
import threading
import flet as ft
from app.core.settings_store import SettingsStore


def _store() -> SettingsStore:
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return SettingsStore(path)


def _safe_int(value, default: int) -> int:
    """Tolerant int parse for TextField values that may be empty / non-numeric.

    Falls back to *default* on TypeError/ValueError so a stray "abc" does not
    raise during save.
    """
    try:
        return int(str(value).strip() or default)
    except (TypeError, ValueError):
        return default


class SettingsView:
    """Settings page grouped into ExpansionTile sections."""

    def __init__(self, page: ft.Page):
        self._page = page
        # Flet 0.84: FilePicker is a Service (not a Control) and is registered
        # via page.services; methods are async and return values directly.
        self._file_picker = ft.FilePicker()
        self._jxl_picker = ft.FilePicker()
        page.services.extend([self._file_picker, self._jxl_picker])

        store = _store()
        store.migrate_from_legacy()
        auth = store.get_section("auth")
        dl = store.get_section("download")
        flt = store.get_section("filter")
        perf = store.get_section("performance")
        jxl = store.get_section("jxl")
        directory = store.get_section("directory")
        self._n_cookies: int = max(1, len(auth.get("cookies_pool", []) or []))

        self._tf_account = ft.TextField(label="帳號", value=auth.get("account", ""), width=300)
        self._tf_password = ft.TextField(label="密碼", value=auth.get("password", ""), width=300, password=True, can_reveal_password=True)
        self._tf_userid = ft.TextField(label="User ID", value=str(auth.get("userid", "")), width=200)
        self._tf_path = ft.TextField(label="下載路徑", value=dl.get("path", ""), expand=True, read_only=True)

        self._sw_hidefollow = ft.Switch(label="隱藏追蹤", value=bool(flt.get("hidefollow", False)))
        self._sw_nogif = ft.Switch(label="過濾 GIF", value=bool(flt.get("nogif", False)))
        self._sw_notag = ft.Switch(label="無 tag 不下載", value=bool(flt.get("notag", False)))
        self._sw_notime = ft.Switch(label="無時間不下載", value=bool(flt.get("notime", False)))
        self._tf_like_num = ft.TextField(label="最低讚數（一般）", value=str(dl.get("like_num", 0)), width=150, keyboard_type=ft.KeyboardType.NUMBER)
        self._tf_r18_like_num = ft.TextField(label="最低讚數（R18）", value=str(dl.get("r18_like_num", 0)), width=150, keyboard_type=ft.KeyboardType.NUMBER)
        self._tf_rescrape_within_days = ft.TextField(
            label="重抓上限（天數，0=關閉）",
            value=str(dl.get("rescrape_within_days", 365)),
            width=200,
            keyboard_type=ft.KeyboardType.NUMBER,
            tooltip="作品距上傳時間小於此天數時，Step 3 會重新抓取 meta 以更新讚數，避免新作品因初始讚數不足被永久過濾。0 表示停用。已下載的作品永遠不會重抓。",
        )
        self._tf_filename_template = ft.TextField(
            label="檔名範本（留空＝使用預設）",
            value=str(dl.get("filename_template", "") or ""),
            hint_text="例：{timetag}_PID{pid}{page}{hashtag}.{ext}",
            expand=True,
        )

        self._ban_tags: list[str] = list(dl.get("ban_tag", []))
        self._must_tags: list[str] = list(dl.get("must_tag", []))
        self._ban_tag_row = ft.Row(wrap=True, spacing=4)
        self._must_tag_row = ft.Row(wrap=True, spacing=4)
        self._tf_ban_input = ft.TextField(label="新增禁止 tag", width=200, on_submit=self._add_ban_tag)
        self._tf_must_input = ft.TextField(label="新增必須 tag", width=200, on_submit=self._add_must_tag)
        self._refresh_tag_rows()

        self._sw_create_dir = ft.Switch(
            label="依作者 ID 建立子資料夾",
            value=bool(directory.get("create_dir", False)),
        )
        self._sw_r18_dir = ft.Switch(
            label="R-18 作品分類資料夾",
            value=not bool(directory.get("no_R18_dir", False)),
        )
        self._sw_r18g_dir = ft.Switch(
            label="R-18G 作品分類資料夾",
            value=not bool(directory.get("no_R18G_dir", False)),
        )
        self._sw_ai_dir = ft.Switch(
            label="AI 生成作品分類資料夾",
            value=bool(directory.get("ai_gen_dir", False)),
        )

        self._sw_jxl = ft.Switch(label="啟用 JXL 轉檔", value=bool(jxl.get("enable", False)))
        self._tf_jxl_path = ft.TextField(label="cjxl.exe 路徑", value=jxl.get("cjxl_path", ""), expand=True, read_only=True)
        self._sw_jxl_delete = ft.Switch(label="刪除原檔", value=bool(jxl.get("delete_original", False)))
        effort_val = max(1, min(9, int(jxl.get("effort", 7))))
        self._sl_jxl_effort = ft.Slider(min=1, max=9, divisions=8, value=effort_val, label="{value}", width=200)

        # Cooldown controls — replace old pid_wait_min / pid_wait_max text fields
        cooldown_avg = int(perf.get("pid_cooldown_avg", 35))
        self._sl_cooldown = ft.Slider(
            min=5, max=300, divisions=59, value=float(cooldown_avg),
            label="{value}", width=240,
            on_change=self._on_cooldown_slider_change,
        )
        self._tf_cooldown = ft.TextField(
            label="平均冷卻秒數",
            value=str(cooldown_avg),
            width=170,
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._on_cooldown_tf_change,
        )
        self._label_cooldown_hint = ft.Text(
            self._cooldown_hint(cooldown_avg),
            size=11,
            color=ft.Colors.RED_600 if cooldown_avg < 30 else ft.Colors.GREY_600,
        )
        self._sw_single_thread = ft.Switch(label="單執行緒 PID 模式", value=bool(perf.get("single_thread_mode", False)))

        # Proxy controls
        proxy_pool = auth.get("proxy_pool") or []
        self._tf_proxy_pool = ft.TextField(
            label="Proxy 列表（每行一個）",
            hint_text="# 一行一個 proxy\nhttp://1.2.3.4:8080\nsocks5://user:pass@host:1080",
            value="\n".join(proxy_pool),
            multiline=True,
            min_lines=4,
            max_lines=15,
            expand=True,
        )
        self._proxy_test_results = ft.Column([], spacing=4)

        # Wait time controls
        intra_min = int(perf.get("intra_pid_wait_min", 5))
        intra_max = int(perf.get("intra_pid_wait_max", 15))
        self._tf_intra_min = ft.TextField(
            value=str(intra_min), width=80, keyboard_type=ft.KeyboardType.NUMBER,
        )
        self._tf_intra_max = ft.TextField(
            value=str(intra_max), width=80, keyboard_type=ft.KeyboardType.NUMBER,
        )
        nocookie_min = int(perf.get("pid_wait_nocookie_min", 3))
        nocookie_max = int(perf.get("pid_wait_nocookie_max", 8))
        self._tf_nocookie_min = ft.TextField(
            value=str(nocookie_min), width=80, keyboard_type=ft.KeyboardType.NUMBER,
        )
        self._tf_nocookie_max = ft.TextField(
            value=str(nocookie_max), width=80, keyboard_type=ft.KeyboardType.NUMBER,
        )

        # User-Agent controls
        self._tf_agent = ft.TextField(
            value=auth.get("agent", ""),
            hint_text="未設定，將使用內建隨機 UA",
            expand=True,
        )
        self._btn_detect_ua = ft.OutlinedButton(
            "重新偵測 Chrome", on_click=self._on_detect_chrome,
        )
        self._label_ua_status = ft.Text("", size=11, color=ft.Colors.GREY_600)

    # ------------------------------------------------------------------
    # File picker handlers
    # ------------------------------------------------------------------

    async def _pick_folder(self, e: ft.ControlEvent) -> None:
        path = await self._file_picker.get_directory_path()
        if path:
            self._tf_path.value = path + "/"
            self._tf_path.update()

    async def _pick_jxl_exe(self, e: ft.ControlEvent) -> None:
        files = await self._jxl_picker.pick_files(allowed_extensions=["exe"])
        if files:
            self._tf_jxl_path.value = files[0].path
            self._tf_jxl_path.update()

    # ------------------------------------------------------------------
    # Tag handlers
    # ------------------------------------------------------------------

    def _add_ban_tag(self, e: ft.ControlEvent) -> None:
        tag = self._tf_ban_input.value.strip()
        if tag and tag not in self._ban_tags:
            self._ban_tags.append(tag)
            self._tf_ban_input.value = ""
            self._refresh_tag_rows()
            self._page.update()

    def _add_must_tag(self, e: ft.ControlEvent) -> None:
        tag = self._tf_must_input.value.strip()
        if tag and tag not in self._must_tags:
            self._must_tags.append(tag)
            self._tf_must_input.value = ""
            self._refresh_tag_rows()
            self._page.update()

    def _refresh_tag_rows(self) -> None:
        self._ban_tag_row.controls = [
            ft.Chip(label=ft.Text(t), on_delete=lambda e, tag=t: self._remove_ban_tag(tag))
            for t in self._ban_tags
        ]
        self._must_tag_row.controls = [
            ft.Chip(label=ft.Text(t), on_delete=lambda e, tag=t: self._remove_must_tag(tag))
            for t in self._must_tags
        ]

    def _remove_ban_tag(self, tag: str) -> None:
        self._ban_tags = [t for t in self._ban_tags if t != tag]
        self._refresh_tag_rows()
        self._page.update()

    def _remove_must_tag(self, tag: str) -> None:
        self._must_tags = [t for t in self._must_tags if t != tag]
        self._refresh_tag_rows()
        self._page.update()

    # ------------------------------------------------------------------
    # Cooldown helpers
    # ------------------------------------------------------------------

    def _cooldown_hint(self, avg) -> str:
        try:
            avg_f = max(1.0, float(avg))
        except (TypeError, ValueError):
            avg_f = 35.0
        n = self._n_cookies
        throughput = avg_f * math.log(n + 1) / n
        suffix = "；推薦 >= 30 秒"
        if n == 1:
            return f"1 個 cookie：每請求約 {throughput:.0f} 秒{suffix}"
        speedup = math.log(2) * n / math.log(n + 1)
        return f"{n} 個 cookie：每請求約 {throughput:.1f} 秒（比單 cookie 快 {speedup:.1f}x）{suffix}"

    def reload_cookie_count(self) -> None:
        """Re-read cookie count from store; refresh hint label in-place."""
        auth = _store().get_section("auth")
        self._n_cookies = max(1, len(auth.get("cookies_pool", []) or []))
        avg = self._safe_int_cooldown()
        self._label_cooldown_hint.value = self._cooldown_hint(avg)
        self._label_cooldown_hint.color = ft.Colors.RED_600 if avg < 30 else ft.Colors.GREY_600
        try:
            self._label_cooldown_hint.update()
        except Exception:
            pass

    def _on_cooldown_slider_change(self, e: ft.ControlEvent) -> None:
        try:
            val = int(e.control.value)
        except (TypeError, ValueError):
            return
        self._tf_cooldown.value = str(val)
        self._label_cooldown_hint.value = self._cooldown_hint(val)
        self._label_cooldown_hint.color = ft.Colors.RED_600 if val < 30 else ft.Colors.GREY_600
        try:
            self._tf_cooldown.update()
            self._label_cooldown_hint.update()
        except Exception:
            pass

    def _on_cooldown_tf_change(self, e: ft.ControlEvent) -> None:
        try:
            val = max(5, min(300, int(self._tf_cooldown.value or "35")))
        except (TypeError, ValueError):
            return
        self._tf_cooldown.value = str(val)            # snap displayed text to clamped value
        self._sl_cooldown.value = float(val)
        self._label_cooldown_hint.value = self._cooldown_hint(val)
        self._label_cooldown_hint.color = ft.Colors.RED_600 if val < 30 else ft.Colors.GREY_600
        try:
            self._tf_cooldown.update()                # flush the TextField update
            self._sl_cooldown.update()
            self._label_cooldown_hint.update()
        except Exception:
            pass

    def _safe_int_cooldown(self) -> int:
        try:
            return max(5, min(300, int(self._tf_cooldown.value or "35")))
        except (TypeError, ValueError):
            return 35

    @staticmethod
    def _clamp_wait(tf: ft.TextField, default: int, lo: int = 1) -> int:
        try:
            return max(lo, int(tf.value or str(default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp_wait_max(tf_min: ft.TextField, tf_max: ft.TextField, default_min: int, default_max: int) -> int:
        try:
            lo = max(1, int(tf_min.value or str(default_min)))
            hi = int(tf_max.value or str(default_max))
            return max(lo, hi)
        except (TypeError, ValueError):
            return default_max

    # ------------------------------------------------------------------
    # Proxy test handler
    # ------------------------------------------------------------------

    def _on_test_proxies(self, e: ft.ControlEvent) -> None:
        from app.core.proxy_utils import parse_proxy_list, test_proxy
        lines = parse_proxy_list(self._tf_proxy_pool.value or "")
        self._proxy_test_results.controls = [ft.Text("測試中...", size=11)]
        try:
            self._page.update()
        except Exception:
            pass

        def _run():
            results = []
            if not lines:
                results = [ft.Text("（無有效 proxy）", size=11, color=ft.Colors.GREY_600)]
            else:
                for url in lines:
                    ok, msg = test_proxy(url, timeout=10)
                    icon = "v" if ok else "x"
                    color = ft.Colors.GREEN_600 if ok else ft.Colors.RED_600
                    results.append(ft.Text(f"{icon} {url} — {msg}", size=11, color=color))
            self._proxy_test_results.controls = results
            try:
                self._page.update()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _on_detect_chrome(self, e: ft.ControlEvent) -> None:
        from app.core.chrome_detect import detect_chrome_ua
        ua = detect_chrome_ua()
        if ua:
            self._tf_agent.value = ua
            version = ua.split("Chrome/")[1].split(" ")[0] if "Chrome/" in ua else ua
            self._label_ua_status.value = f"已從登錄檔偵測到 Chrome {version}，UA 已更新"
            self._label_ua_status.color = ft.Colors.GREEN_600
        else:
            self._label_ua_status.value = "找不到 Chrome 安裝（已檢查登錄檔與 AppData），請手動填寫 UA"
            self._label_ua_status.color = ft.Colors.RED_600
        try:
            self._tf_agent.update()
            self._label_ua_status.update()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(self) -> None:
        store = _store()
        from app.core.proxy_utils import parse_proxy_list
        auth_existing = store.get_section("auth")
        store.update_section("auth", {
            **auth_existing,
            "account": self._tf_account.value,
            "password": self._tf_password.value,
            "userid": self._tf_userid.value,
            "proxy_pool": parse_proxy_list(self._tf_proxy_pool.value or ""),
            "agent": self._tf_agent.value.strip(),
        })
        store.update_section("download", {
            **store.get_section("download"),
            "path": self._tf_path.value,
            "like_num": _safe_int(self._tf_like_num.value, 0),
            "r18_like_num": _safe_int(self._tf_r18_like_num.value, 0),
            "rescrape_within_days": _safe_int(self._tf_rescrape_within_days.value, 0),
            "ban_tag": self._ban_tags,
            "must_tag": self._must_tags,
            "filename_template": (self._tf_filename_template.value or "").strip(),
        })
        store.update_multiple({
            "filter": {
                **store.get_section("filter"),
                "hidefollow": self._sw_hidefollow.value,
                "nogif": self._sw_nogif.value,
                "notag": self._sw_notag.value,
                "notime": self._sw_notime.value,
            },
            "directory": {
                **store.get_section("directory"),
                "create_dir": bool(self._sw_create_dir.value),
                "no_R18_dir": not bool(self._sw_r18_dir.value),
                "no_R18G_dir": not bool(self._sw_r18g_dir.value),
                "ai_gen_dir": bool(self._sw_ai_dir.value),
            },
            "performance": {
                **store.get_section("performance"),
                "single_thread_mode": self._sw_single_thread.value,
                "pid_cooldown_avg": self._safe_int_cooldown(),
                "pid_wait_nocookie_min": self._clamp_wait(self._tf_nocookie_min, 3, lo=1),
                "pid_wait_nocookie_max": self._clamp_wait_max(self._tf_nocookie_min, self._tf_nocookie_max, 3, 8),
                "intra_pid_wait_min": self._clamp_wait(self._tf_intra_min, 5, lo=1),
                "intra_pid_wait_max": self._clamp_wait_max(self._tf_intra_min, self._tf_intra_max, 5, 15),
            },
            "jxl": {
                "enable": self._sw_jxl.value,
                "cjxl_path": self._tf_jxl_path.value,
                "delete_original": self._sw_jxl_delete.value,
                "effort": int(self._sl_jxl_effort.value),
            },
        })

    def _save_and_notify(self, e) -> None:
        avg_val = self._safe_int_cooldown()
        if avg_val < 30:
            def _confirm(ev):
                try:
                    self._page.pop_dialog()
                except Exception:
                    pass
                self.save()
                try:
                    self._page.show_dialog(ft.SnackBar(ft.Text("設定已儲存"), duration=1500))
                except Exception:
                    pass

            def _cancel(ev):
                try:
                    self._page.pop_dialog()
                except Exception:
                    pass

            try:
                self._page.show_dialog(ft.AlertDialog(
                    title=ft.Text("冷卻時間偏短"),
                    content=ft.Text(
                        f"平均冷卻 {avg_val} 秒低於建議值 30 秒，\n可能被 Pixiv 風控偵測。確定要套用？"
                    ),
                    actions=[
                        ft.TextButton("取消", on_click=_cancel),
                        ft.FilledButton("確定套用", on_click=_confirm),
                    ],
                ))
            except Exception:
                # If dialog fails for any reason, fall back to direct save
                self.save()
            return
        self.save()
        try:
            self._page.show_dialog(ft.SnackBar(ft.Text("設定已儲存"), duration=1500))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def build(self) -> ft.Column:
        def _tile(title: str, controls: list) -> ft.ExpansionTile:
            return ft.ExpansionTile(
                title=ft.Text(title),
                controls=[ft.Container(
                    content=ft.Column(controls, spacing=8),
                    padding=ft.Padding.only(left=16, top=10, bottom=12),
                )],
            )

        save_btn = ft.FilledButton("儲存設定", icon=ft.Icons.SAVE, on_click=self._save_and_notify)

        return ft.Column(
            controls=[
                ft.Text("設定", size=20, weight=ft.FontWeight.BOLD),
                _tile("帳號設定", [
                    self._tf_account,
                    self._tf_password,
                    self._tf_userid,
                    ft.Row([self._tf_path, ft.IconButton(
                        icon=ft.Icons.FOLDER_OPEN,
                        on_click=self._pick_folder,
                    )]),
                ]),
                _tile("過濾規則", [
                    ft.Row([self._sw_hidefollow, self._sw_nogif, self._sw_notag, self._sw_notime], wrap=True),
                    ft.Row([self._tf_like_num, self._tf_r18_like_num, self._tf_rescrape_within_days], spacing=16, wrap=True),
                ]),
                _tile("檔名範本", [
                    ft.Text(
                        "可用佔位符：{pid} {page} {ext} {tag} {hashtag} {timetag} {date} {time}\n"
                        "留空時使用預設規則。檔名會自動清掉 Windows 不允許的字元。",
                        size=11, color=ft.Colors.GREY_700,
                    ),
                    self._tf_filename_template,
                ]),
                _tile("資料夾分類", [
                    ft.Text(
                        "啟用後將在下載目錄底下，依作品屬性建立子資料夾。",
                        size=11, color=ft.Colors.GREY_700,
                    ),
                    self._sw_create_dir,
                    self._sw_r18_dir,
                    self._sw_r18g_dir,
                    self._sw_ai_dir,
                ]),
                _tile("標籤過濾", [
                    ft.Text("禁止 tag", size=12),
                    self._ban_tag_row,
                    ft.Row([self._tf_ban_input, ft.IconButton(icon=ft.Icons.ADD, on_click=self._add_ban_tag)]),
                    ft.Text("必須 tag", size=12),
                    self._must_tag_row,
                    ft.Row([self._tf_must_input, ft.IconButton(icon=ft.Icons.ADD, on_click=self._add_must_tag)]),
                ]),
                _tile("JXL 轉檔", [
                    self._sw_jxl,
                    ft.Row([self._tf_jxl_path, ft.IconButton(
                        icon=ft.Icons.FILE_OPEN,
                        on_click=self._pick_jxl_exe,
                    )]),
                    self._sw_jxl_delete,
                    ft.Row([ft.Text("Effort（1-9）"), self._sl_jxl_effort]),
                ]),
                _tile("冷卻設定", [
                    ft.Row([self._tf_cooldown, self._label_cooldown_hint], spacing=12),
                    self._sl_cooldown,
                    ft.Row(
                        [ft.Text("同 PID 頁間等待（秒）", size=13),
                         self._tf_intra_min, ft.Text("~"), self._tf_intra_max],
                        spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [ft.Text("免 Cookie 請求等待（秒）", size=13),
                         self._tf_nocookie_min, ft.Text("~"), self._tf_nocookie_max],
                        spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._sw_single_thread,
                ]),
                _tile("Proxy 設定", [
                    self._tf_proxy_pool,
                    ft.Row([
                        ft.OutlinedButton("測試全部 Proxy", on_click=self._on_test_proxies),
                    ]),
                    self._proxy_test_results,
                ]),
                _tile("User-Agent 設定", [
                    ft.Row([self._tf_agent, self._btn_detect_ua], spacing=8),
                    self._label_ua_status,
                ]),
                ft.Container(content=save_btn, padding=ft.Padding.only(top=8)),
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=0,
            expand=True,
        )
