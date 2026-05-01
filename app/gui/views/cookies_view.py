from __future__ import annotations
import os
import queue
import threading
import flet as ft
from app.core.settings_store import SettingsStore
from app.core.pixiv_thread_utils import normalize_cookie_entries
from app.core.worker_event import WorkerEvent

DEFAULT_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)

_STATUS_COLORS = {
    "有效":   ft.Colors.GREEN_600,
    "失效":   ft.Colors.RED_600,
    "測試中": ft.Colors.BLUE_400,
    "未知":   ft.Colors.GREY_600,
}


def _store() -> SettingsStore:
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return SettingsStore(path)


class CookiesView:
    """Cookie pool management: list, add, edit, remove, test (with checkbox)."""

    def __init__(self, page: ft.Page, event_q: queue.Queue):
        self._page = page
        self._event_q = event_q
        self._entries: list[dict] = []
        self._selected: set[str] = set()
        self._testing: bool = False
        self._load_entries()

        self._count_text = ft.Text("", color=ft.Colors.GREY_600)
        self._select_all_cb = ft.Checkbox(
            label="全選", value=False, on_change=self._on_toggle_all,
        )
        self._btn_test_selected = ft.FilledButton(
            "測試選取", icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_test_selected,
        )
        self._btn_test_all = ft.OutlinedButton(
            "測試全部", icon=ft.Icons.PLAYLIST_PLAY,
            on_click=self._on_test_all,
        )
        self._btn_auto_pair = ft.OutlinedButton(
            "自動配對", icon=ft.Icons.AUTO_FIX_HIGH,
            on_click=self._on_auto_pair,
        )
        self._table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("選取")),
                ft.DataColumn(ft.Text("別名")),
                ft.DataColumn(ft.Text("狀態")),
                ft.DataColumn(ft.Text("Cookie 預覽")),
                ft.DataColumn(ft.Text("Proxy 綁定")),
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
        self._agent = str(auth.get("agent") or "").strip() or DEFAULT_AGENT
        self._proxy_pool: list[str] = list(auth.get("proxy_pool") or [])
        self._cookie_proxy_map: dict[str, str | None] = dict(auth.get("cookie_proxy_map") or {})

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
            "cookie_proxy_map": self._cookie_proxy_map,
        })

    def _refresh_table(self) -> None:
        self._table.rows = []
        for i, entry in enumerate(self._entries):
            alias = entry.get("alias", "") or f"Cookie {i+1}"
            cookie = entry.get("cookie", "")
            status = entry.get("status", "未知")
            preview = cookie[:30] + "..." if len(cookie) > 30 else cookie
            status_color = _STATUS_COLORS.get(status, ft.Colors.GREY_600)
            current_proxy = self._cookie_proxy_map.get(cookie) or ""
            proxy_options = [ft.dropdown.Option(key="", text="（本機 IP）")] + [
                ft.dropdown.Option(key=p, text=p[:50]) for p in self._proxy_pool
            ]
            proxy_dd = ft.Dropdown(
                options=proxy_options,
                value=current_proxy if (current_proxy == "" or current_proxy in self._proxy_pool) else "",
                width=220,
                on_change=lambda e, c=cookie: self._on_proxy_change(c, e.control.value),
            )
            self._table.rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Checkbox(
                    value=cookie in self._selected,
                    on_change=lambda e, c=cookie: self._on_toggle_row(c, e.control.value),
                )),
                ft.DataCell(ft.Text(alias)),
                ft.DataCell(ft.Text(status, color=status_color, weight=ft.FontWeight.BOLD)),
                ft.DataCell(ft.Text(preview, size=11, font_family="monospace")),
                ft.DataCell(proxy_dd),
                ft.DataCell(ft.Row([
                    ft.IconButton(icon=ft.Icons.EDIT, tooltip="編輯",
                                  on_click=lambda e, idx=i: self._open_edit_dialog(idx)),
                    ft.IconButton(icon=ft.Icons.DELETE, tooltip="刪除",
                                  icon_color=ft.Colors.RED_400,
                                  on_click=lambda e, idx=i: self._remove_entry(idx)),
                ])),
            ]))
        self._count_text.value = f"（共 {len(self._entries)} 筆，已選 {len(self._selected)}）"
        all_cookies = {e.get("cookie", "") for e in self._entries if e.get("cookie")}
        self._select_all_cb.value = (
            bool(all_cookies) and self._selected.issuperset(all_cookies)
        )
        self._btn_test_selected.disabled = self._testing or not self._selected
        self._btn_test_all.disabled = self._testing or not self._entries
        self._btn_auto_pair.disabled = not self._entries or not self._proxy_pool

    def _on_toggle_row(self, cookie: str, value: bool) -> None:
        if value:
            self._selected.add(cookie)
        else:
            self._selected.discard(cookie)
        # Refresh header (count + select-all + test button enable) without
        # rebuilding rows, so the user's checkbox click isn't visually replaced.
        all_cookies = {e.get("cookie", "") for e in self._entries if e.get("cookie")}
        self._count_text.value = f"（共 {len(self._entries)} 筆，已選 {len(self._selected)}）"
        self._select_all_cb.value = (
            bool(all_cookies) and self._selected.issuperset(all_cookies)
        )
        self._btn_test_selected.disabled = self._testing or not self._selected
        try:
            self._page.update()
        except Exception:
            pass

    def _on_toggle_all(self, e: ft.ControlEvent) -> None:
        if bool(e.control.value):
            self._selected = {x.get("cookie", "") for x in self._entries if x.get("cookie")}
        else:
            self._selected.clear()
        self._refresh_table()
        try:
            self._page.update()
        except Exception:
            pass

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
        cookie = self._entries[idx].get("cookie", "")
        self._entries.pop(idx)
        self._selected.discard(cookie)
        self._cookie_proxy_map.pop(cookie, None)
        self._save_entries()
        self._refresh_table()
        self._page.update()

    def _on_proxy_change(self, cookie: str, proxy_url: str) -> None:
        self._cookie_proxy_map[cookie] = proxy_url or None
        self._save_entries()

    def _on_auto_pair(self, e: ft.ControlEvent) -> None:
        cookies = [
            entry.get("cookie", "")
            for entry in self._entries
            if entry.get("cookie", "").strip()
        ]
        for i, cookie in enumerate(cookies):
            if i < len(self._proxy_pool):
                self._cookie_proxy_map[cookie] = self._proxy_pool[i]
            else:
                self._cookie_proxy_map[cookie] = None
        self._save_entries()
        self._refresh_table()
        try:
            self._page.update()
        except Exception:
            pass

    def _on_test_selected(self, e: ft.ControlEvent) -> None:
        cookies = [x.get("cookie", "") for x in self._entries
                   if x.get("cookie", "") in self._selected]
        self._start_tests(cookies)

    def _on_test_all(self, e: ft.ControlEvent) -> None:
        cookies = [x.get("cookie", "") for x in self._entries
                   if x.get("cookie", "").strip()]
        self._start_tests(cookies)

    def _start_tests(self, cookies: list[str]) -> None:
        if not cookies or self._testing:
            return
        self._testing = True
        targets = set(cookies)
        for entry in self._entries:
            if entry.get("cookie", "") in targets:
                entry["status"] = "測試中"
        self._refresh_table()
        try:
            self._page.update()
        except Exception:
            pass
        # Network I/O on a daemon thread; results route back through event_q
        # so apply_cookie_test_result runs on the asyncio event loop.
        threading.Thread(
            target=self._do_tests, args=(cookies, self._agent), daemon=True,
        ).start()

    def _do_tests(self, cookies: list[str], agent: str) -> None:
        # Imported here to keep the module import lightweight at app start.
        from app.core import pixiv_api
        try:
            for cookie in cookies:
                ok = False
                try:
                    count, _ = pixiv_api.Test_cookies([cookie], agent)
                    ok = int(count) > 0
                except Exception:
                    ok = False
                self._event_q.put(WorkerEvent(
                    "cookie_status", (cookie, "有效" if ok else "失效"),
                ))
        finally:
            self._event_q.put(WorkerEvent("cookie_status", ("__done__", "")))

    def apply_cookie_test_result(self, cookie: str, status: str) -> None:
        """Called by the dispatcher (event-loop thread) when a test finishes."""
        if cookie == "__done__":
            self._testing = False
            self._refresh_table()
            return
        for entry in self._entries:
            if entry.get("cookie", "") == cookie:
                entry["status"] = status
                break
        self._refresh_table()

    def reload_from_settings(self) -> None:
        """Reload entries + proxy_pool from settings (e.g., after settings tab edit).

        Safe to call from the event loop only.
        """
        self._load_entries()
        self._refresh_table()
        try:
            self._page.update()
        except Exception:
            pass

    def build(self) -> ft.Column:
        header = ft.Row([
            ft.Text("Cookies", size=20, weight=ft.FontWeight.BOLD),
            self._count_text,
            ft.FilledButton("+ 新增", icon=ft.Icons.ADD,
                            on_click=lambda e: self._open_edit_dialog(None)),
            self._select_all_cb,
            self._btn_test_selected,
            self._btn_test_all,
            self._btn_auto_pair,
        ], alignment=ft.MainAxisAlignment.START, spacing=12, wrap=True)

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
