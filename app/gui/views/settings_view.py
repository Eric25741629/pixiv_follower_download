from __future__ import annotations
import os
import threading
import flet as ft
from app.core.settings_store import SettingsStore
from app.gui import components as c
from app.gui.glass import current_theme, glass_snackbar
from app.gui.views.settings_handlers import _SettingsHandlersMixin
from app import i18n
import contextlib


# Locale display names are endonyms (shown in their own language), not translated
# per current UI locale. Codes come from i18n.available_locales(); unknown codes
# fall back to the raw code so a newly-dropped-in locale still appears.
_LOCALE_DISPLAY_NAMES = {"zh-TW": "中文（繁體）", "en": "English"}


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


class SettingsView(_SettingsHandlersMixin):
    """Settings page grouped into ExpansionTile sections."""

    def __init__(self, page: ft.Page, on_saved=None):
        self._page = page
        # Optional callback fired after any persist (explicit save + autosave
        # toggles) so a running task can be notified to apply live settings.
        self._on_saved = on_saved
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
        ui = store.get_section("ui")
        self._n_cookies: int = max(1, len(auth.get("cookies_pool", []) or []))

        theme = current_theme(page)

        # 介面語言（apply-on-restart）。語言名稱顯示為各自的語言（endonym），
        # 不隨目前 UI 語言翻譯。
        self._dd_language = c.dropdown(
            theme,
            label=i18n.t("settings.lang.label"),
            value=str(ui.get("language", i18n.BASE_LOCALE) or i18n.BASE_LOCALE),
            options=[
                (code, _LOCALE_DISPLAY_NAMES.get(code, code))
                for code in i18n.available_locales()
            ],
            width=220,
            on_select=self._on_language_select,
        )

        self._tf_account = c.text_field(theme, label=i18n.t("settings.account.account"), value=auth.get("account", ""), width=300)
        self._tf_password = c.text_field(theme, label=i18n.t("settings.account.password"), value=auth.get("password", ""), width=300, password=True)
        self._tf_userid = c.text_field(theme, label=i18n.t("settings.account.userid"), value=str(auth.get("userid", "")), width=200)
        self._tf_path = c.text_field(theme, label=i18n.t("settings.download.path"), value=dl.get("path", ""), expand=True, read_only=True)

        self._sw_hidefollow = c.switch(theme, label=i18n.t("settings.filter.hidefollow"), value=bool(flt.get("hidefollow", False)))
        self._sw_nogif = c.switch(theme, label=i18n.t("settings.filter.nogif"), value=bool(flt.get("nogif", False)))
        self._sw_notag = c.switch(theme, label=i18n.t("settings.tags.notag"), value=bool(flt.get("notag", False)))
        self._sw_notime = c.switch(theme, label=i18n.t("settings.tags.notime"), value=bool(flt.get("notime", False)))
        self._tf_like_num = c.number_field(theme, label=i18n.t("settings.filter.like_num"), value=dl.get("like_num", 0), width=150)
        self._tf_r18_like_num = c.number_field(theme, label=i18n.t("settings.filter.r18_like_num"), value=dl.get("r18_like_num", 0), width=150)
        self._tf_rescrape_within_days = c.number_field(
            theme,
            label=i18n.t("settings.filter.rescrape_days"),
            value=dl.get("rescrape_within_days", 365),
            width=200,
            tooltip=i18n.t("settings.filter.rescrape_days_tooltip"),
        )
        self._tf_filename_template = c.text_field(
            theme,
            label=i18n.t("settings.filename.template_label"),
            value=str(dl.get("filename_template", "") or ""),
            hint_text=i18n.t("settings.filename.template_hint"),
            expand=True,
        )
        self._tf_download_time = c.text_field(
            theme,
            label=i18n.t("settings.filename.download_time"),
            value=str(dl.get("download_time", "") or ""),
            hint_text=i18n.t("settings.filename.download_time_hint"),
            width=320,
            tooltip=i18n.t("settings.filename.download_time_tooltip"),
        )
        self._sw_set_file_mtime = c.switch(
            theme,
            label=i18n.t("settings.filename.set_mtime"),
            value=bool(dl.get("set_file_mtime", True)),
            tooltip=i18n.t("settings.filename.set_mtime_tooltip"),
        )
        self._sw_tag_strip_brackets = c.switch(
            theme,
            label=i18n.t("settings.tags.strip_brackets"),
            value=bool(dl.get("tag_strip_brackets", False)),
            tooltip=i18n.t("settings.tags.strip_brackets_tooltip"),
        )
        self._sw_tag_strip_special_chars = c.switch(
            theme,
            label=i18n.t("settings.tags.strip_special"),
            value=bool(dl.get("tag_strip_special_chars", False)),
            tooltip=i18n.t("settings.tags.strip_special_tooltip"),
        )
        self._sw_author_order = c.switch(
            theme,
            label=i18n.t("settings.source.author_order"),
            value=bool(dl.get("author_order", False)),
            tooltip=i18n.t("settings.source.author_order_tooltip"),
        )
        self._sw_combined_mode = c.switch(
            theme,
            label=i18n.t("settings.source.combined"),
            value=bool(dl.get("combined_mode", False)),
            tooltip=i18n.t("settings.source.combined_tooltip"),
        )
        self._tf_combined_workers = c.number_field(
            theme,
            label=i18n.t("settings.source.workers"),
            value=int(dl.get("combined_workers", 1)),
            width=200,
            tooltip=i18n.t("settings.source.workers_tooltip"),
        )
        self._sw_force_rescan = c.switch(
            theme,
            label=i18n.t("settings.source.force_rescan"),
            value=bool(dl.get("force_full_rescan", False)),
            tooltip=i18n.t("settings.source.force_rescan_tooltip"),
        )
        self._dd_source_mode = c.dropdown(
            theme,
            label=i18n.t("settings.source.mode"),
            value=str(dl.get("source_mode", "following") or "following"),
            options=[
                ("following", i18n.t("settings.source.mode.following")),
                ("bookmarks", i18n.t("settings.source.mode.bookmarks")),
            ],
            width=220,
        )
        self._dd_following_scope = c.dropdown(
            theme,
            label=i18n.t("settings.source.following_scope"),
            value=str(dl.get("following_scope", "all") or "all"),
            options=[
                ("public", i18n.t("settings.source.following.public")),
                ("private", i18n.t("settings.source.following.private")),
                ("all", i18n.t("settings.source.following.all")),
            ],
            width=220,
        )
        self._dd_bookmark_scope = c.dropdown(
            theme,
            label=i18n.t("settings.source.bookmark_scope"),
            value=str(dl.get("bookmark_scope", "all") or "all"),
            options=[
                ("public", i18n.t("settings.source.bookmark.public")),
                ("private", i18n.t("settings.source.bookmark.private")),
                ("all", i18n.t("settings.source.bookmark.all")),
            ],
            width=220,
        )

        sch = store.get_section("schedule")
        self._sw_schedule_enabled = c.switch(
            theme,
            label=i18n.t("settings.adv.schedule_enabled"),
            value=bool(sch.get("enabled", False)),
        )
        self._dd_schedule_mode = c.dropdown(
            theme,
            label=i18n.t("settings.adv.schedule_mode"),
            value=str(sch.get("mode", "daily")),
            options=[("daily", i18n.t("settings.adv.schedule.daily")),
                     ("interval", i18n.t("settings.adv.schedule.interval"))],
            width=200,
        )
        self._tf_schedule_time = c.text_field(
            theme, label=i18n.t("settings.adv.schedule_time"), value=str(sch.get("time", "03:00")), width=160,
        )
        self._tf_schedule_interval = c.text_field(
            theme, label=i18n.t("settings.adv.schedule_interval"), value=str(sch.get("interval_hours", 6)), width=160,
        )

        self._ban_tags: list[str] = list(dl.get("ban_tag", []))
        self._must_tags: list[str] = list(dl.get("must_tag", []))
        self._ban_tag_row = ft.Row(wrap=True, spacing=4)
        self._must_tag_row = ft.Row(wrap=True, spacing=4)
        self._tf_ban_input = c.text_field(theme, label=i18n.t("settings.filter.new_ban"), width=200)
        self._tf_ban_input.on_submit = self._add_ban_tag
        self._tf_must_input = c.text_field(theme, label=i18n.t("settings.filter.new_must"), width=200)
        self._tf_must_input.on_submit = self._add_must_tag
        self._refresh_tag_rows()

        self._sw_create_dir = c.switch(
            theme,
            label=i18n.t("settings.dir.create_dir"),
            value=bool(directory.get("create_dir", False)),
        )
        self._sw_r18_dir = c.switch(
            theme,
            label=i18n.t("settings.dir.r18"),
            value=not bool(directory.get("no_R18_dir", False)),
        )
        self._sw_r18g_dir = c.switch(
            theme,
            label=i18n.t("settings.dir.r18g"),
            value=not bool(directory.get("no_R18G_dir", False)),
        )
        self._sw_ai_dir = c.switch(
            theme,
            label=i18n.t("settings.dir.ai"),
            value=bool(directory.get("ai_gen_dir", False)),
        )

        self._sw_jxl = c.switch(theme, label=i18n.t("settings.adv.jxl_enable"), value=bool(jxl.get("enable", False)))
        self._tf_jxl_path = c.text_field(theme, label=i18n.t("settings.adv.jxl_path"), value=jxl.get("cjxl_path", ""), expand=True, read_only=True)
        self._sw_jxl_delete = c.switch(theme, label=i18n.t("settings.adv.jxl_delete"), value=bool(jxl.get("delete_original", False)))
        self._sw_jxl_skip_gif = c.switch(theme, label=i18n.t("settings.adv.jxl_skip_gif"), value=bool(jxl.get("skip_gif", True)))
        effort_val = max(1, min(9, int(jxl.get("effort", 7))))
        self._sl_jxl_effort = c.slider(theme, min=1, max=9, divisions=8, value=effort_val, label="{value}", width=200)

        # Cooldown controls — replace old pid_wait_min / pid_wait_max text fields
        cooldown_avg = int(perf.get("pid_cooldown_avg", 35))
        self._sl_cooldown = c.slider(
            theme,
            min=0, max=300, divisions=60, value=float(cooldown_avg),
            label="{value}", width=240,
            on_change=self._on_cooldown_slider_change,
        )
        self._tf_cooldown = c.number_field(
            theme,
            label=i18n.t("settings.adv.cooldown_label"),
            value=cooldown_avg,
            width=170,
            on_change=self._on_cooldown_tf_change,
        )
        self._label_cooldown_hint = ft.Text(
            self._cooldown_hint(cooldown_avg),
            size=11,
            color=self._cooldown_hint_color(cooldown_avg),
        )

        # Proxy controls
        proxy_pool = auth.get("proxy_pool") or []
        self._tf_proxy_pool = c.multiline_field(
            theme,
            label=i18n.t("settings.proxy.label"),
            hint_text=i18n.t("settings.proxy.hint"),
            value="\n".join(proxy_pool),
            min_lines=4,
            max_lines=15,
        )
        self._proxy_test_results = ft.Column([], spacing=4)

        # Wait time controls
        intra_min = int(perf.get("intra_pid_wait_min", 5))
        intra_max = int(perf.get("intra_pid_wait_max", 15))
        self._tf_intra_min = c.number_field(theme, label=None, value=intra_min, width=80)
        self._tf_intra_max = c.number_field(theme, label=None, value=intra_max, width=80)
        nocookie_min = int(perf.get("pid_wait_nocookie_min", 3))
        nocookie_max = int(perf.get("pid_wait_nocookie_max", 8))
        self._tf_nocookie_min = c.number_field(theme, label=None, value=nocookie_min, width=80)
        self._tf_nocookie_max = c.number_field(theme, label=None, value=nocookie_max, width=80)

        # User-Agent controls
        self._tf_agent = c.text_field(
            theme,
            label=None,
            value=auth.get("agent", ""),
            hint_text=i18n.t("settings.ua.hint"),
            expand=True,
        )
        self._btn_detect_ua = c.secondary_button(
            i18n.t("settings.ua.detect_btn"), on_click=self._on_detect_chrome,
        )
        self._label_ua_status = ft.Text("", size=11, color=theme.text_secondary)

        # Flipping any Switch persists that one field immediately (主動紀錄),
        # so a toggle sticks without the user having to click 「儲存設定」.
        # Text fields / sliders still rely on the explicit save button.
        self._wire_switch_autosave()

    # _cooldown_hint_color / the per-switch autosave wiring
    # (_switch_autosave_map / _wire_switch_autosave / _make_autosave_handler)
    # moved to settings_handlers._SettingsHandlersMixin (file-size refactor);
    # inherited.

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

    # Cooldown helpers (_cooldown_hint / reload_cookie_count /
    # _on_cooldown_slider_change / _on_cooldown_tf_change / _safe_int_cooldown)
    # moved to settings_handlers._SettingsHandlersMixin (file-size refactor);
    # inherited. _clamp_wait / _clamp_wait_max stay (save-path helpers).

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
        theme = current_theme(self._page)
        lines = parse_proxy_list(self._tf_proxy_pool.value or "")
        self._proxy_test_results.controls = [ft.Text(i18n.t("settings.proxy.testing"), size=11)]
        with contextlib.suppress(Exception):
            self._page.update()

        def _run():
            results = []
            if not lines:
                results = [ft.Text(i18n.t("settings.proxy.none"), size=11, color=theme.text_secondary)]
            else:
                for url in lines:
                    ok, msg = test_proxy(url, timeout=10)
                    icon = "v" if ok else "x"
                    color = theme.success if ok else theme.error
                    results.append(ft.Text(f"{icon} {url} — {msg}", size=11, color=color))
            self._proxy_test_results.controls = results
            with contextlib.suppress(Exception):
                self._page.update()

        threading.Thread(target=_run, daemon=True).start()

    def _on_detect_chrome(self, e: ft.ControlEvent) -> None:
        from app.core.chrome_detect import detect_chrome_ua
        theme = current_theme(self._page)
        ua = detect_chrome_ua()
        if ua:
            self._tf_agent.value = ua
            version = ua.split("Chrome/")[1].split(" ")[0] if "Chrome/" in ua else ua
            self._label_ua_status.value = i18n.t("settings.ua.detected", version=version)
            self._label_ua_status.color = theme.success
        else:
            self._label_ua_status.value = i18n.t("settings.ua.not_found")
            self._label_ua_status.color = theme.error
        try:
            self._tf_agent.update()
            self._label_ua_status.update()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _persist_language(self, code: str) -> None:
        """Persist ui.language, preserving the rest of the ui section."""
        _store().update_fields("ui", {"language": str(code or i18n.BASE_LOCALE)})

    def _on_language_select(self, e: ft.ControlEvent) -> None:
        # File I/O off the event loop (matches the theme-save pattern). Language
        # is apply-on-restart, so there is nothing to re-render now.
        code = str(self._dd_language.value or i18n.BASE_LOCALE)
        threading.Thread(
            target=self._persist_language, args=(code,), daemon=True,
        ).start()

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
            "rescrape_within_days": _safe_int(self._tf_rescrape_within_days.value, 365),
            "ban_tag": self._ban_tags,
            "must_tag": self._must_tags,
            "filename_template": (self._tf_filename_template.value or "").strip(),
            "download_time": (self._tf_download_time.value or "").strip(),
            "set_file_mtime": bool(self._sw_set_file_mtime.value),
            "tag_strip_brackets": bool(self._sw_tag_strip_brackets.value),
            "tag_strip_special_chars": bool(self._sw_tag_strip_special_chars.value),
            "author_order": bool(self._sw_author_order.value),
            "combined_mode": bool(self._sw_combined_mode.value),
            "combined_workers": max(1, _safe_int(self._tf_combined_workers.value, 1)),
            "source_mode": str(self._dd_source_mode.value or "following"),
            "following_scope": str(self._dd_following_scope.value or "all"),
            "bookmark_scope": str(self._dd_bookmark_scope.value or "all"),
        })
        store.update_section("schedule", {
            "enabled": bool(self._sw_schedule_enabled.value),
            "mode": str(self._dd_schedule_mode.value or "daily"),
            "time": str(self._tf_schedule_time.value or "03:00"),
            "interval_hours": int(self._tf_schedule_interval.value or 6)
                if str(self._tf_schedule_interval.value or "").strip().isdigit() else 6,
            "action": "run_all",
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
                "skip_gif": self._sw_jxl_skip_gif.value,
            },
        })
        if self._on_saved is not None:
            with contextlib.suppress(Exception):
                self._on_saved()

    def _saved_snackbar(self) -> ft.SnackBar:
        snack = glass_snackbar(current_theme(self._page), i18n.t("settings.saved"))
        snack.duration = 1500
        return snack

    def _save_and_notify(self, e) -> None:
        theme = current_theme(self._page)
        avg_val = self._safe_int_cooldown()
        if avg_val < 30:
            def _confirm(ev):
                with contextlib.suppress(Exception):
                    self._page.pop_dialog()
                self.save()
                with contextlib.suppress(Exception):
                    self._page.show_dialog(self._saved_snackbar())

            def _cancel(ev):
                with contextlib.suppress(Exception):
                    self._page.pop_dialog()

            try:
                self._page.show_dialog(c.confirm_dialog(
                    theme,
                    title=i18n.t("settings.cooldown_warn.title"),
                    content=ft.Text(
                        i18n.t("settings.cooldown_warn.body", avg=avg_val),
                        color=theme.text_secondary,
                    ),
                    on_confirm=_confirm,
                    confirm_text=i18n.t("settings.cooldown_warn.confirm"),
                    on_cancel=_cancel,
                ))
            except Exception:
                # If dialog fails for any reason, fall back to direct save
                self.save()
            return
        self.save()
        with contextlib.suppress(Exception):
            self._page.show_dialog(self._saved_snackbar())

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def build(self) -> ft.Column:
        theme = current_theme(self._page)

        save_btn = c.primary_button(i18n.t("settings.save"), icon=ft.Icons.SAVE, on_click=self._save_and_notify)

        def _sec(key: str, controls: list) -> ft.Control:
            # Every content section leads with a one-line purpose note (spec §B
            # 顯示整理「每個主區塊都要有一句用途說明」), then its controls.
            return c.section(theme, i18n.t(f"settings.section.{key}"),
                             [c.note(theme, i18n.t(f"settings.note.{key}")), *controls])

        return ft.Column(
            controls=[
                c.page_title(theme, i18n.t("settings.title")),
                c.section(theme, i18n.t("settings.section.interface"), [
                    self._dd_language,
                    c.note(theme, i18n.t("settings.lang.restart_hint")),
                ]),
                # 1. 帳號與連線（帳號 + User-Agent + Proxy）
                _sec("account", [
                    c.subhead(theme, i18n.t("settings.sub.pixiv_account")),
                    self._tf_account,
                    self._tf_password,
                    self._tf_userid,
                    c.subhead(theme, i18n.t("settings.sub.user_agent")),
                    ft.Row([self._tf_agent, self._btn_detect_ua], spacing=8),
                    self._label_ua_status,
                    c.subhead(theme, i18n.t("settings.sub.proxy")),
                    self._tf_proxy_pool,
                    ft.Row([
                        c.secondary_button(i18n.t("settings.proxy.test_all"), on_click=self._on_test_proxies),
                    ]),
                    self._proxy_test_results,
                ]),
                # 2. 作品來源與抓取策略
                _sec("source", [
                    c.subhead(theme, i18n.t("settings.source.mode")),
                    c.note(theme, i18n.t("settings.note.source_modes")),
                    ft.Row(
                        [self._dd_source_mode, self._dd_following_scope, self._dd_bookmark_scope],
                        spacing=16,
                        wrap=True,
                    ),
                    self._sw_combined_mode,
                    ft.Row([self._tf_combined_workers], spacing=16, wrap=True),
                    self._sw_author_order,
                    self._sw_force_rescan,
                ]),
                # 3. 作品篩選（篩選條件 + 缺值處理）
                _sec("filter", [
                    ft.Row([self._sw_hidefollow, self._sw_nogif], wrap=True),
                    ft.Row([self._tf_like_num, self._tf_r18_like_num, self._tf_rescrape_within_days], spacing=16, wrap=True),
                    c.subhead(theme, i18n.t("settings.sub.missing_value")),
                    ft.Row([self._sw_notag, self._sw_notime], wrap=True),
                    c.subhead(theme, i18n.t("settings.sub.ban_tag")),
                    self._ban_tag_row,
                    ft.Row([self._tf_ban_input, c.icon_action(ft.Icons.ADD, on_click=self._add_ban_tag)]),
                    c.subhead(theme, i18n.t("settings.sub.must_tag")),
                    self._must_tag_row,
                    ft.Row([self._tf_must_input, c.icon_action(ft.Icons.ADD, on_click=self._add_must_tag)]),
                ]),
                # 4. 下載與檔名（位置 + 資料夾分類 + 檔名 + 時間戳 + tag 整理）
                _sec("download", [
                    c.subhead(theme, i18n.t("settings.sub.download_path")),
                    ft.Row([self._tf_path, c.icon_action(
                        ft.Icons.FOLDER_OPEN,
                        on_click=self._pick_folder,
                    )]),
                    c.subhead(theme, i18n.t("settings.sub.folder_classify")),
                    c.note(theme, i18n.t("settings.note.folder_classify")),
                    self._sw_create_dir,
                    self._sw_r18_dir,
                    self._sw_r18g_dir,
                    self._sw_ai_dir,
                    c.subhead(theme, i18n.t("settings.sub.filename_template")),
                    c.note(theme, i18n.t("settings.note.filename_template")),
                    self._tf_filename_template,
                    c.subhead(theme, i18n.t("settings.sub.timestamp")),
                    c.note(theme, i18n.t("settings.note.timestamp")),
                    self._tf_download_time,
                    self._sw_set_file_mtime,
                    c.subhead(theme, i18n.t("settings.sub.tag_organize")),
                    ft.Row(
                        [self._sw_tag_strip_brackets, self._sw_tag_strip_special_chars],
                        wrap=True,
                    ),
                ]),
                # 5. 格式轉換、效能與自動化
                _sec("advanced", [
                    c.subhead(theme, i18n.t("settings.sub.jxl")),
                    self._sw_jxl,
                    ft.Row([self._tf_jxl_path, c.icon_action(
                        ft.Icons.FILE_OPEN,
                        on_click=self._pick_jxl_exe,
                    )]),
                    self._sw_jxl_delete,
                    self._sw_jxl_skip_gif,
                    ft.Row([ft.Text(i18n.t("settings.adv.jxl_effort"), color=theme.text_primary), self._sl_jxl_effort]),
                    c.subhead(theme, i18n.t("settings.sub.cooldown")),
                    ft.Row([self._tf_cooldown, self._label_cooldown_hint], spacing=12),
                    self._sl_cooldown,
                    ft.Row(
                        [c.inline_label(theme, i18n.t("settings.adv.intra_wait")),
                         self._tf_intra_min, ft.Text("~", color=theme.text_secondary), self._tf_intra_max],
                        spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [c.inline_label(theme, i18n.t("settings.adv.nocookie_wait")),
                         self._tf_nocookie_min, ft.Text("~", color=theme.text_secondary), self._tf_nocookie_max],
                        spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    c.subhead(theme, i18n.t("settings.sub.schedule")),
                    self._sw_schedule_enabled,
                    self._dd_schedule_mode,
                    self._tf_schedule_time,
                    self._tf_schedule_interval,
                ]),
                ft.Container(content=save_btn, padding=ft.Padding.only(top=8)),
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=theme.gap,
            expand=True,
        )
