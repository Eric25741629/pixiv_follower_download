from __future__ import annotations
import os
import flet as ft
from app.core.settings_store import SettingsStore
from app.core.pixiv_thread_utils import normalize_cookie_entries


def _store() -> SettingsStore:
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return SettingsStore(path)


class CookiesView:
    """Cookie pool management: list, add, edit, remove."""

    def __init__(self, page: ft.Page):
        self._page = page
        self._entries: list[dict] = []
        self._load_entries()
        self._table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("別名")),
                ft.DataColumn(ft.Text("狀態")),
                ft.DataColumn(ft.Text("Cookie 預覽")),
                ft.DataColumn(ft.Text("操作")),
            ],
            rows=[],
        )
        self._refresh_table()

    def _load_entries(self) -> None:
        store = _store()
        store.migrate_from_legacy()
        auth = store.get_section("auth")
        alias_map = auth.get("cookies_aliases", {})
        if not isinstance(alias_map, dict):
            alias_map = {}
        raw = auth.get("cookies_entries", []) or auth.get("cookies_pool", [])
        self._entries = normalize_cookie_entries(raw, alias_map=alias_map)

    def _save_entries(self) -> None:
        store = _store()
        auth = store.get_section("auth")
        pool = [x.get("cookie", "") for x in self._entries if x.get("cookie", "").strip()]
        alias_map = {
            x["cookie"]: x.get("alias", "")
            for x in self._entries if x.get("cookie", "").strip()
        }
        store.update_section("auth", {
            **auth,
            "cookies_entries": self._entries,
            "cookies_pool": pool,
            "cookies_aliases": alias_map,
            "cookies": pool[0] if pool else "",
        })

    def _refresh_table(self) -> None:
        self._table.rows = []
        for i, entry in enumerate(self._entries):
            alias = entry.get("alias", "") or f"Cookie {i+1}"
            cookie = entry.get("cookie", "")
            status = entry.get("status", "未知")
            preview = cookie[:30] + "..." if len(cookie) > 30 else cookie
            status_color = (
                ft.Colors.GREEN_600 if status == "有效"
                else ft.Colors.RED_600 if status == "失效"
                else ft.Colors.GREY_600
            )
            self._table.rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(alias)),
                ft.DataCell(ft.Text(status, color=status_color)),
                ft.DataCell(ft.Text(preview, size=11, font_family="monospace")),
                ft.DataCell(ft.Row([
                    ft.IconButton(icon=ft.Icons.EDIT, tooltip="編輯",
                                  on_click=lambda e, idx=i: self._open_edit_dialog(idx)),
                    ft.IconButton(icon=ft.Icons.DELETE, tooltip="刪除",
                                  icon_color=ft.Colors.RED_400,
                                  on_click=lambda e, idx=i: self._remove_entry(idx)),
                ])),
            ]))

    def _open_edit_dialog(self, idx: int | None) -> None:
        entry = self._entries[idx] if idx is not None else {}
        tf_alias = ft.TextField(label="別名（例：主帳號）", value=entry.get("alias", ""), width=300)
        tf_cookie = ft.TextField(
            label="Cookie 字串", value=entry.get("cookie", ""),
            multiline=True, min_lines=3, max_lines=6, width=500,
        )

        def save_dialog(e: ft.ControlEvent) -> None:
            new_entry = {
                "cookie": tf_cookie.value.strip(),
                "alias": tf_alias.value.strip(),
                "status": entry.get("status", "未知"),
            }
            if idx is None:
                self._entries.append(new_entry)
            else:
                self._entries[idx] = new_entry
            self._save_entries()
            self._refresh_table()
            self._page.pop_dialog()
            self._page.update()

        def cancel_dialog(e: ft.ControlEvent) -> None:
            self._page.pop_dialog()

        dialog = ft.AlertDialog(
            title=ft.Text("編輯 Cookie" if idx is not None else "新增 Cookie"),
            content=ft.Column([tf_alias, tf_cookie], tight=True, spacing=12),
            actions=[
                ft.TextButton("取消", on_click=cancel_dialog),
                ft.FilledButton("儲存", on_click=save_dialog),
            ],
        )
        self._page.show_dialog(dialog)

    def _remove_entry(self, idx: int) -> None:
        self._entries.pop(idx)
        self._save_entries()
        self._refresh_table()
        self._page.update()

    def build(self) -> ft.Column:
        header = ft.Row([
            ft.Text("Cookies", size=20, weight=ft.FontWeight.BOLD),
            ft.Text(f"（共 {len(self._entries)} 筆）", color=ft.Colors.GREY_600),
            ft.FilledButton("+ 新增", icon=ft.Icons.ADD,
                            on_click=lambda e: self._open_edit_dialog(None)),
        ], alignment=ft.MainAxisAlignment.START, spacing=12)

        return ft.Column(
            controls=[
                header,
                ft.Container(
                    content=ft.Column([self._table], scroll=ft.ScrollMode.AUTO),
                    expand=True,
                ),
            ],
            expand=True,
            spacing=12,
        )
