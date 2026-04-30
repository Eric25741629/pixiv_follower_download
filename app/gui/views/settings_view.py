from __future__ import annotations
import os
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

        self._tf_dl_wait_min = ft.TextField(label="等待最小秒數", value=str(perf.get("pid_wait_min", 10)), width=120, keyboard_type=ft.KeyboardType.NUMBER)
        self._tf_dl_wait_max = ft.TextField(label="等待最大秒數", value=str(perf.get("pid_wait_max", 60)), width=120, keyboard_type=ft.KeyboardType.NUMBER)
        self._sw_single_thread = ft.Switch(label="單執行緒 PID 模式", value=bool(perf.get("single_thread_mode", False)))

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

    def save(self) -> None:
        store = _store()
        auth = store.get_section("auth")
        store.update_section("auth", {
            **auth,
            "account": self._tf_account.value,
            "password": self._tf_password.value,
            "userid": self._tf_userid.value,
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
                "pid_wait_min": int(self._tf_dl_wait_min.value or 10),
                "pid_wait_max": int(self._tf_dl_wait_max.value or 60),
            },
            "jxl": {
                "enable": self._sw_jxl.value,
                "cjxl_path": self._tf_jxl_path.value,
                "delete_original": self._sw_jxl_delete.value,
                "effort": int(self._sl_jxl_effort.value),
            },
        })

    def build(self) -> ft.Column:
        def _tile(title: str, controls: list) -> ft.ExpansionTile:
            return ft.ExpansionTile(
                title=ft.Text(title),
                controls=[ft.Container(
                    content=ft.Column(controls, spacing=8),
                    padding=ft.padding.only(left=16, bottom=12),
                )],
            )

        def _save_and_notify(e):
            self.save()
            self._page.show_dialog(ft.SnackBar(ft.Text("設定已儲存"), duration=1500))

        save_btn = ft.FilledButton("儲存設定", icon=ft.Icons.SAVE, on_click=_save_and_notify)

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
                _tile("下載設定", [
                    ft.Row([self._tf_dl_wait_min, self._tf_dl_wait_max], spacing=16),
                    self._sw_single_thread,
                ]),
                ft.Container(content=save_btn, padding=ft.padding.only(top=8)),
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=0,
            expand=True,
        )
