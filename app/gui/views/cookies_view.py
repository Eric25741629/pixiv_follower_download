from __future__ import annotations
import os
import queue
import threading
import flet as ft
from app.core.app_logging import get_logger
from app.core.settings_store import SettingsStore
from app.core.pixiv_thread_utils import normalize_cookie_entries
from app.core.worker_event import WorkerEvent
from app.gui import components as c
from app.gui.glass import current_theme, glass_dialog, glass_panel, glass_pill

_log = get_logger("pixiv.cookies_view")

DEFAULT_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)


def _status_colors(page: ft.Page) -> dict[str, str]:
    """Map cookie validity states to theme semantic tokens."""
    t = current_theme(page)
    return {
        "有效":   t.success,
        "失效":   t.error,
        "測試中": t.info,
        "未知":   t.text_muted,
    }


def _format_tested_at(value) -> str:
    """Render last_tested_at (epoch float) as 'M月D日 HH:MM' / '剛剛' / '—'."""
    if value is None or value == "":
        return "—"
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return "—"
    import time as _time
    delta = _time.time() - ts
    if delta < 60:
        return "剛剛"
    if delta < 3600:
        return f"{int(delta // 60)} 分鐘前"
    local = _time.localtime(ts)
    return _time.strftime("%m月%d日 %H:%M", local)


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

        theme = current_theme(page)
        self._count_text = ft.Text("", color=theme.text_secondary)
        self._select_all_cb = ft.Checkbox(
            label="全選", value=False, on_change=self._on_toggle_all,
        )
        self._btn_test_selected = glass_pill(
            "測試選取", theme, primary=True, width=120,
            on_click=self._on_test_selected,
        )
        self._btn_test_all = glass_pill(
            "測試全部", theme, width=120,
            on_click=self._on_test_all,
        )
        self._btn_enable_selected = glass_pill(
            "啟用選取", theme, width=120,
            on_click=lambda e: self._set_enabled_for_selected(True),
        )
        self._btn_disable_selected = glass_pill(
            "禁用選取", theme, width=120,
            on_click=lambda e: self._set_enabled_for_selected(False),
        )
        self._btn_auto_pair = glass_pill(
            "自動配對", theme, width=120,
            on_click=self._on_auto_pair,
        )
        # Replaced DataTable with a ListView of compact custom rows.
        # DataTable in Flet 0.84 has a fixed natural width per column and
        # never shrinks to viewport — so columns past ~1000 px (the Proxy
        # dropdown + Cookie 預覽 combo) used to fall outside the visible
        # area on a 1100-wide window. ListView lets each row reflow with
        # the page width and keeps every control reachable without a
        # horizontal scroll.
        self._table = ft.ListView(spacing=4, padding=0, expand=True)
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

    def _build_proxy_dropdown(self, cookie):
        """Build the per-row proxy dropdown control."""
        current_proxy = self._cookie_proxy_map.get(cookie) or ""
        options = [ft.dropdown.Option(key="", text="（本機 IP）")] + [
            ft.dropdown.Option(key=p, text=p[:40]) for p in self._proxy_pool
        ]
        return ft.Dropdown(
            options=options,
            value=current_proxy if (current_proxy == "" or current_proxy in self._proxy_pool) else "",
            width=180,
            text_size=11,
            content_padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            on_select=lambda e, ck=cookie: self._on_proxy_change(ck, e.control.value),
        )

    def _build_status_badge(self, status, status_color):
        """Compact pill showing the cookie validity state (semantic color on 35% alpha fill)."""
        return ft.Container(
            content=ft.Text(status, color=status_color, size=10, weight=ft.FontWeight.BOLD),
            bgcolor="#59" + status_color[1:],
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            border_radius=10,
        )

    def _build_cookie_row(self, idx, entry):
        """Build one compact card row that reflows with the page width."""
        theme = current_theme(self._page)
        alias = entry.get("alias", "") or f"Cookie {idx+1}"
        cookie = entry.get("cookie", "")
        status = entry.get("status", "未知")
        preview_short = (cookie[:24] + "...") if len(cookie) > 24 else cookie
        status_color = _status_colors(self._page).get(status, theme.text_muted)
        tested_text = _format_tested_at(entry.get("last_tested_at"))
        enabled = entry.get("enabled") is not False
        alias_color = theme.text_primary if enabled else theme.text_muted

        # Left cluster: checkbox + per-row enable switch (always visible).
        controls_left = ft.Row(
            controls=[
                ft.Checkbox(
                    value=cookie in self._selected,
                    on_change=lambda e, ck=cookie: self._on_toggle_row(ck, e.control.value),
                ),
                c.switch(
                    theme, label=None,
                    value=enabled,
                    tooltip="關閉後本次任務不使用此 Cookie",
                    on_change=lambda e, ck=cookie: self._on_toggle_enabled(ck, e.control.value),
                ),
            ],
            spacing=0,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Middle cluster: alias + status badge + last-tested + cookie preview.
        # Each sub-cell is a fixed-width Container so badges/timestamps line
        # up vertically across rows regardless of how long an alias is.
        # Long aliases are ellipsised by the Text widget.
        alias_cell = ft.Container(
            content=ft.Text(
                alias, color=alias_color, weight=ft.FontWeight.BOLD, size=13,
                overflow=ft.TextOverflow.ELLIPSIS, max_lines=1, no_wrap=True,
                tooltip=alias if len(alias) > 14 else None,
            ),
            width=140,
        )
        badge_cell = ft.Container(
            content=self._build_status_badge(status, status_color),
            width=44,
        )
        tested_cell = ft.Container(
            content=ft.Text(f"檢查：{tested_text}", size=11, color=theme.text_secondary,
                            overflow=ft.TextOverflow.ELLIPSIS, max_lines=1, no_wrap=True),
            width=130,
        )
        info_header = ft.Row(
            controls=[alias_cell, badge_cell, tested_cell],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        info_preview = ft.Text(
            preview_short, size=10, color=theme.text_muted,
            font_family="monospace", tooltip=cookie,
            overflow=ft.TextOverflow.ELLIPSIS, no_wrap=True,
        )
        # Bounded width so wrap=True on the outer Row can decide whether the
        # right cluster (proxy + edit/delete) fits beside us or flows below.
        # expand=True is intentionally NOT used: under Flet 0.85's Row(wrap=True)
        # — which becomes a Wrap widget — `expand` is undefined and the column
        # consumes the entire ListView vertical space (silently hiding all
        # subsequent rows).
        controls_middle = ft.Container(
            content=ft.Column([info_header, info_preview], spacing=2),
            width=420,
        )

        # Right cluster: proxy binding + edit/delete actions.
        btn_delete = c.icon_action(
            ft.Icons.DELETE, tooltip="刪除",
            on_click=lambda e, i=idx: self._remove_entry(i),
        )
        btn_delete.icon_color = theme.error
        controls_right = ft.Row(
            controls=[
                self._build_proxy_dropdown(cookie),
                c.icon_action(
                    ft.Icons.EDIT, tooltip="編輯",
                    on_click=lambda e, i=idx: self._open_edit_dialog(i),
                ),
                btn_delete,
            ],
            spacing=2,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        return glass_panel(
            ft.Row(
                controls=[controls_left, controls_middle, controls_right],
                spacing=10,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            theme,
            padding=12,
            radius=theme.radius_sm,
        )

    def _refresh_table_header_state(self):
        """Update the count label, select-all checkbox, and button-disabled flags."""
        self._count_text.value = f"（共 {len(self._entries)} 筆，已選 {len(self._selected)}）"
        all_cookies = {e.get("cookie", "") for e in self._entries if e.get("cookie")}
        self._select_all_cb.value = (
            bool(all_cookies) and self._selected.issuperset(all_cookies)
        )
        self._btn_test_selected.disabled = self._testing or not self._selected
        self._btn_test_all.disabled = self._testing or not self._entries
        self._btn_enable_selected.disabled = not self._selected
        self._btn_disable_selected.disabled = not self._selected
        self._btn_auto_pair.disabled = not self._entries

    def _refresh_table(self) -> None:
        self._table.controls = [
            self._build_cookie_row(i, entry) for i, entry in enumerate(self._entries)
        ]
        self._refresh_table_header_state()

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
        self._btn_enable_selected.disabled = not self._selected
        self._btn_disable_selected.disabled = not self._selected
        try:
            self._page.update()
        except Exception:
            pass

    def _set_enabled_for_selected(self, enable: bool) -> None:
        """Bulk flip the ``enabled`` flag for every selected cookie."""
        if not self._selected:
            return
        targets = set(self._selected)
        changed = False
        for entry in self._entries:
            if entry.get("cookie", "") not in targets:
                continue
            if enable:
                if entry.get("enabled") is False:
                    entry.pop("enabled", None)
                    changed = True
            else:
                if entry.get("enabled") is not False:
                    entry["enabled"] = False
                    changed = True
        if changed:
            self._save_entries()
        self._refresh_table()
        try:
            self._page.update()
        except Exception:
            pass

    def _on_toggle_enabled(self, cookie: str, value: bool) -> None:
        """Persist the per-cookie enable flag, then rebuild only that row.

        Targeted rebuild keeps the Switch click responsive — a full
        ``_refresh_table()`` would visually replace the user's toggle
        mid-animation.
        """
        for i, entry in enumerate(self._entries):
            if entry.get("cookie", "") != cookie:
                continue
            if value:
                entry.pop("enabled", None)
            else:
                entry["enabled"] = False
            self._save_entries()
            try:
                self._table.controls[i] = self._build_cookie_row(i, entry)
                self._table.update()
            except Exception:
                pass
            break
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
        theme = current_theme(self._page)
        entry = self._entries[idx] if idx is not None else {}
        tf_alias = c.text_field(
            theme, label="別名（例：主帳號）", value=entry.get("alias", ""), width=300,
        )
        tf_cookie = c.multiline_field(
            theme, label="Cookie 字串", value=entry.get("cookie", ""),
            min_lines=3, max_lines=6, expand=False,
        )
        tf_cookie.width = 500

        def save_dialog(e: ft.ControlEvent) -> None:
            new_cookie = tf_cookie.value.strip()
            cookie_changed = new_cookie != str(entry.get("cookie", "")).strip()
            # Spread the original entry first so persisted keys (last_tested_at,
            # enabled, and any future fields) survive an alias-only edit — the old
            # code rebuilt the dict from scratch and silently re-enabled a disabled
            # cookie + wiped its trust cache. If the cookie STRING itself changed,
            # the old test result no longer applies, so reset status/last_tested_at.
            new_entry = {**entry, "cookie": new_cookie, "alias": tf_alias.value.strip()}
            if cookie_changed:
                new_entry["status"] = "未知"
                new_entry.pop("last_tested_at", None)
            elif "status" not in new_entry:
                new_entry["status"] = "未知"
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

        dialog = glass_dialog(
            theme,
            "編輯 Cookie" if idx is not None else "新增 Cookie",
            ft.Column([tf_alias, tf_cookie], tight=True, spacing=12),
            actions=[
                ft.TextButton("取消", on_click=cancel_dialog),
                c.primary_button("儲存", on_click=save_dialog),
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
        import time as _time
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
                    "cookie_status",
                    (cookie, "有效" if ok else "失效", _time.time()),
                ))
        finally:
            self._event_q.put(WorkerEvent("cookie_status", ("__done__", "", None)))

    def apply_cookie_test_result(
        self, cookie: str, status: str, tested_at: float | None = None,
    ) -> None:
        """Called by the dispatcher (event-loop thread) when a test finishes.
        tested_at is an epoch timestamp (None for sentinel/in-progress events)."""
        if cookie == "__done__":
            self._testing = False
            # All in-flight cookie_status events have been applied to
            # self._entries by now (they came in earlier on the same
            # queue), so flush the new statuses + timestamps to disk.
            try:
                self._save_entries()
            except Exception:
                pass
            self._refresh_table()
            return
        for entry in self._entries:
            if entry.get("cookie", "") == cookie:
                entry["status"] = status
                if tested_at is not None:
                    entry["last_tested_at"] = float(tested_at)
                break
        self._refresh_table()

    def reload_from_settings(self) -> None:
        """Reload entries + proxy_pool from settings (e.g., after settings tab edit).

        Safe to call from the event loop only. Exceptions are logged but
        never re-raised — propagating them to Flet's click dispatch has
        previously caused the entire session to reset, killing any
        running download.
        """
        try:
            self._load_entries()
            self._refresh_table()
        except Exception:
            _log.exception("reload_from_settings failed")
            return
        try:
            self._page.update()
        except Exception:
            _log.exception("page.update in reload_from_settings failed")

    def build(self) -> ft.Column:
        header = ft.Row([
            c.page_title(current_theme(self._page), "Cookies"),
            self._count_text,
            glass_pill("+ 新增", current_theme(self._page), primary=True, width=120,
                       on_click=lambda e: self._open_edit_dialog(None)),
            self._select_all_cb,
            self._btn_test_selected,
            self._btn_test_all,
            self._btn_enable_selected,
            self._btn_disable_selected,
            self._btn_auto_pair,
        ], alignment=ft.MainAxisAlignment.START, spacing=12, wrap=True)

        # self._table is a ListView (vertical scroll built-in). Each row is
        # a Container with an inner wrap=True Row, so the proxy dropdown +
        # edit/delete cluster reflows below the alias/status info instead
        # of disappearing off-screen on narrow windows.
        return ft.Column(
            controls=[
                header,
                ft.Container(content=self._table, expand=True),
            ],
            expand=True,
            spacing=12,
        )
