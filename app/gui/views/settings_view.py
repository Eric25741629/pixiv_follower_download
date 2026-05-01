from __future__ import annotations
import os
import threading
import flet as ft
from app.core.settings_store import SettingsStore


def _store() -> SettingsStore:
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return SettingsStore(path)


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

        self._ban_tags: list[str] = list(dl.get("ban_tag", []))
        self._must_tags: list[str] = list(dl.get("must_tag", []))
        self._ban_tag_row = ft.Row(wrap=True, spacing=4)
        self._must_tag_row = ft.Row(wrap=True, spacing=4)
        self._tf_ban_input = ft.TextField(label="新增禁止 tag", width=200, on_submit=self._add_ban_tag)
        self._tf_must_input = ft.TextField(label="新增必須 tag", width=200, on_submit=self._add_must_tag)
        self._refresh_tag_rows()

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
            width=110,
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
        multiplier = 60.0 / avg_f
        return f"相當於倍率 {multiplier:.1f}x；推薦 >= 30 秒"

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
        })
        store.update_section("download", {
            **store.get_section("download"),
            "path": self._tf_path.value,
            "like_num": int(self._tf_like_num.value or 0),
            "r18_like_num": int(self._tf_r18_like_num.value or 0),
            "ban_tag": self._ban_tags,
            "must_tag": self._must_tags,
        })
        store.update_multiple({
            "filter": {
                **store.get_section("filter"),
                "hidefollow": self._sw_hidefollow.value,
                "nogif": self._sw_nogif.value,
                "notag": self._sw_notag.value,
                "notime": self._sw_notime.value,
            },
            "performance": {
                "single_thread_mode": self._sw_single_thread.value,
                "pid_cooldown_avg": self._safe_int_cooldown(),
                "pid_wait_nocookie_min": int(store.get_section("performance").get("pid_wait_nocookie_min", 1)),
                "pid_wait_nocookie_max": int(store.get_section("performance").get("pid_wait_nocookie_max", 6)),
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
                    padding=ft.padding.only(left=16, bottom=12),
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
                    ft.Row([self._tf_like_num, self._tf_r18_like_num], spacing=16),
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
                    ft.Row([self._tf_cooldown, self._sl_cooldown], spacing=12),
                    self._label_cooldown_hint,
                    self._sw_single_thread,
                ]),
                _tile("Proxy 設定", [
                    self._tf_proxy_pool,
                    ft.Row([
                        ft.OutlinedButton("測試全部 Proxy", on_click=self._on_test_proxies),
                    ]),
                    self._proxy_test_results,
                ]),
                ft.Container(content=save_btn, padding=ft.padding.only(top=8)),
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=0,
            expand=True,
        )
