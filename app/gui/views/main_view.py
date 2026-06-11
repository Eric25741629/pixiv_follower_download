from __future__ import annotations
import contextlib
import queue
import threading
import time
import flet as ft

from app.core.worker_event import WorkerEvent
from app.gui.glass import (
    current_theme,
    glass_dialog,
    glass_panel,
    glass_pill,
    state_colors,
)
from app.gui.log_panel import LogPanel


STEP_LABELS = ["步驟 1\n抓追蹤", "步驟 2\n抓 PID", "步驟 3\n抓 URL", "步驟 4\n下載"]
_BOOKMARK_STEP_LABELS = ["步驟 1\n略過追隨", "步驟 2\n抓收藏 PID"]

# When 邊查邊下 (download.combined_mode) is on, step 3 absorbs step 4: the
# step-3 card is relabeled and the step-4 card is hidden (see
# MainView.apply_combined_mode).
_MERGED_STEP3_LABEL = "步驟 3+4\n邊查邊下"


def _state_palette(page: ft.Page) -> dict[str, tuple[str, str]]:
    """Step-card state colors from the glass design system (call sites unchanged)."""
    return state_colors(current_theme(page))


# Shared geometry for the two stacked progress bars (整體進度 / 本作分頁). Both
# rows use the SAME leading-label width, trailing-count width and spacing so the
# expand=True bars start and end at exactly the same x — otherwise they look
# misaligned (the old layout indented the page row 24px and reserved a wildly
# different trailing width on each row).
_PROG_LEAD_W = 84      # leading label column ("整體進度" / "本作分頁")
_PROG_TRAIL_W = 210    # trailing count column (fits "PID 1234567 分頁 12/15")
_PROG_BAR_HEIGHT = 12  # thicker than the default ~4px hairline
_PROG_BAR_RADIUS = 6
_PROG_ROW_SPACING = 12


def _settings_base_path() -> str:
    import os
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return path


class MainView:
    """The primary workflow view: step cards, controls, progress, log."""

    def __init__(self, page: ft.Page, event_q: queue.Queue):
        self._page = page
        self._event_q = event_q
        self._active_thread = None
        self._run_controller = None  # injected by flet_app after construction
        self._step_states: list[str] = ["idle", "idle", "idle", "idle"]

        self._step_card_containers: list[ft.Container] = []
        self._step_card_texts: list[ft.Text] = []
        self._step_cards = [self._make_step_card(i) for i in range(4)]
        # Whether the step 3+4 cards are merged (邊查邊下); applied in build()
        # from settings and refreshed when the user returns to the 主頁 tab.
        self._combined_mode = False

        # 控制鈕維持原 Button 類別（flet_app 會以字串指派 _btn_pause.content，
        # Container 版 glass_pill 的 content 只收 Control），改以 ButtonStyle
        # 取得玻璃膠囊外觀 — 行為/事件不變。
        self._btn_run_all = ft.FilledButton(
            "▶ 一鍵執行", on_click=self._on_run_all,
            style=self._glass_button_style(primary=True),
        )
        self._btn_pause = ft.OutlinedButton(
            "⏸ 暫停", on_click=self._on_pause_toggle, disabled=True,
            style=self._glass_button_style(primary=False),
        )
        self._btn_stop = ft.OutlinedButton(
            "⏹ 停止", on_click=self._on_stop, disabled=True,
            style=self._glass_button_style(primary=False),
        )
        self._is_paused = False
        self._cards_disabled = False

        # ── 整體進度 / 本作分頁 bars ─────────────────────────────────────────
        # Built by ONE shared helper so the two rows are IDENTICAL in both
        # construction and update path (see _make_progress_row /
        # _render_progress_row). Both start hidden and are revealed on their
        # first real update. The reveal (visible False -> True) is load-bearing:
        # it forces Flet to lay the row out with real content. An always-visible
        # row first laid out with EMPTY children renders degenerate and later
        # .value patches never reflow it — that was the 整體進度 freeze (本作分頁
        # never froze precisely because it was visible-gated; now both are).
        self._progress_bar, self._progress_text, self._progress_row = (
            self._make_progress_row("整體進度")
        )
        self._page_progress_bar, self._page_progress_text, self._page_progress_row = (
            self._make_progress_row("本作分頁")
        )
        self._page_progress_value = 0
        self._page_progress_total = 0
        self._page_progress_pid = ""

        # ── meta line (ETA + cooldown countdown), indented under the bars ────
        self._eta_text = ft.Text(
            "", size=12, color=current_theme(page).text_secondary
        )
        self._countdown_text = ft.Text(
            "", size=12, color=current_theme(page).warning,
            weight=ft.FontWeight.BOLD,
        )
        self._meta_row = ft.Row(
            controls=[
                ft.Container(width=_PROG_LEAD_W),
                self._eta_text,
                self._countdown_text,
            ],
            spacing=18,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._progress_value = 0
        self._progress_total = 0
        self._progress_started_at: float | None = None

        self._phase_ring = ft.ProgressRing(width=14, height=14, stroke_width=2)
        self._phase_label = ft.Text(
            "",
            size=11,
            color=current_theme(page).info,
            expand=True,
        )
        self._phase_row = ft.Row(
            controls=[self._phase_ring, self._phase_label],
            spacing=6,
            visible=False,
        )

        # Modal overlay shown while a step is launching or stopping.
        self._loading_msg = ft.Text(
            "正在啟動...", size=15, weight=ft.FontWeight.BOLD,
            color=current_theme(page).text_primary,
        )
        self._loading_dialog = glass_dialog(
            current_theme(page),
            "",
            ft.Column(
                controls=[
                    ft.ProgressRing(
                        width=56, height=56, stroke_width=4,
                        color=current_theme(page).accent,
                    ),
                    self._loading_msg,
                    ft.Text(
                        "請勿關閉視窗", size=12,
                        color=current_theme(page).text_secondary,
                    ),
                ],
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=14,
            ),
        )
        self._loading_dialog.modal = True
        self._loading_open = False
        self._loading_lock = threading.Lock()

        # Log 面板（單一 selectable Text + 意圖驅動跟隨狀態機 + 膠囊）
        # 全部封裝在 LogPanel（app/gui/log_panel.py）。
        self._log_panel = LogPanel()

        self._source_mode = "following"
        self._following_scope = "all"
        self._bookmark_scope = "all"
        self._active_scope = "all"
        self._mode_title = ft.Text(
            "", size=13, weight=ft.FontWeight.BOLD,
            color=current_theme(page).text_primary,
        )
        self._mode_subtitle = ft.Text(
            "", size=12, color=current_theme(page).text_secondary
        )
        self._scope_label = ft.Text(
            "追隨範圍",
            size=12,
            weight=ft.FontWeight.BOLD,
            color=current_theme(page).text_secondary,
        )
        self._btn_source_following = self._make_mode_button(
            "抓追隨", False, lambda e: self._on_source_mode_change("following")
        )
        self._btn_source_bookmarks = self._make_mode_button(
            "抓收藏", False, lambda e: self._on_source_mode_change("bookmarks")
        )
        self._btn_scope_public = self._make_mode_button(
            "公開", False, lambda e: self._on_scope_change("public")
        )
        self._btn_scope_private = self._make_mode_button(
            "非公開", False, lambda e: self._on_scope_change("private")
        )
        self._btn_scope_all = self._make_mode_button(
            "全部", False, lambda e: self._on_scope_change("all")
        )
        self._scope_row = ft.Row(
            controls=[
                self._scope_label,
                self._btn_scope_public,
                self._btn_scope_private,
                self._btn_scope_all,
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        # Backward-compatible alias for older tests / callers.
        self._bookmark_scope_row = self._scope_row
        self._source_mode_controls = ft.Row(
            controls=[self._btn_source_following, self._btn_source_bookmarks],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._source_mode_slot = ft.Container(
            content=self._source_mode_controls,
            expand=1,
            alignment=ft.Alignment(x=0, y=0),
        )
        self._scope_slot = ft.Container(
            content=self._scope_row,
            expand=2,
            alignment=ft.Alignment(x=1, y=0),
        )
        self._mode_band = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Column(
                        controls=[self._mode_title, self._mode_subtitle],
                        spacing=2,
                        expand=2,
                    ),
                    self._source_mode_slot,
                    self._scope_slot,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            border=ft.Border.all(1, current_theme(page).panel_border),
        )

    def _glass_button_style(self, *, primary: bool) -> ft.ButtonStyle:
        """Pill-shaped glass ButtonStyle matching glass_pill colors.

        Used for the run/pause/stop buttons, which must stay real Buttons
        (their ``content`` is assigned plain strings by flet_app's restore
        path; a glass_pill Container only accepts Control content).
        """
        t = current_theme(self._page)
        fg = "#FFFFFF" if (primary and t.name == "dark") else (
            t.text_primary if primary else t.text_secondary)
        return ft.ButtonStyle(
            color=fg,
            bgcolor=t.accent_fill if primary else "#12FFFFFF",
            side=ft.BorderSide(1, t.panel_border),
            shape=ft.RoundedRectangleBorder(radius=999),
        )

    def _make_step_card(self, index: int) -> ft.Container:
        theme = current_theme(self._page)
        palette = _state_palette(self._page)
        bg, fg = palette["idle"]
        text = ft.Text(
            STEP_LABELS[index],
            text_align=ft.TextAlign.CENTER,
            size=13,
            color=fg,
        )
        container = ft.Container(
            content=text,
            padding=12,
            bgcolor=bg,
            border_radius=theme.radius_sm,
            border=ft.Border.all(1, theme.panel_border),
            width=110,
            alignment=ft.Alignment(x=0, y=0),
            ink=True,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            on_click=lambda e, n=index + 1: self._on_run_step(n),
        )
        self._step_card_containers.append(container)
        self._step_card_texts.append(text)
        return container

    def set_step_state(self, index: int, state: str) -> None:
        """Update step card color. state: 'idle'|'running'|'done'|'error'"""
        self._step_states[index] = state
        palette = _state_palette(self._page)
        bg, fg = palette.get(state, palette["idle"])
        self._step_card_containers[index].bgcolor = bg
        self._step_card_texts[index].color = fg

    def refresh_theme(self) -> None:
        """Re-apply theme-dependent colors after a light/dark toggle.

        Step cards, phase label — anything that picks colors from
        ``current_theme(page)`` rather than auto-themed Flet components.
        Best-effort: swallows ``update()`` errors on detached controls.
        """
        palette = _state_palette(self._page)
        for i, state in enumerate(self._step_states):
            bg, fg = palette.get(state, palette["idle"])
            self._step_card_containers[i].bgcolor = bg
            self._step_card_texts[i].color = fg
        self._phase_label.color = current_theme(self._page).info
        for c in self._step_card_containers:
            try:
                c.update()
            except Exception:
                pass
        for t in self._step_card_texts:
            try:
                t.update()
            except Exception:
                pass
        try:
            self._phase_label.update()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Combined mode (邊查邊下) — merge step 3+4 cards into one
    # ------------------------------------------------------------------

    @staticmethod
    def _read_combined_mode_setting() -> bool:
        """Read download.combined_mode from settings (best-effort)."""
        try:
            import os
            from app.core.settings_store import SettingsStore
            base = os.getenv("APPDATA") + r"/pixiv_download/"
            return bool(
                SettingsStore(base).get_section("download").get("combined_mode", False)
            )
        except Exception:
            return False

    def apply_combined_mode(self, enabled: bool) -> None:
        """Merge step 3+4 into a single card when *enabled*.

        Step 3's card is relabeled 「步驟 3+4 邊查邊下」 and the step-4 card is
        hidden (the Row reflows). When disabled both revert to the default
        4-card layout. Step-state colors are untouched — combined mode still
        runs as step 3 (card index 2), so progress / done coloring is correct,
        and clicking the merged card still launches step 3 → the combined
        thread via the store flag.
        """
        self._combined_mode = bool(enabled)
        self._step_card_texts[2].value = (
            _MERGED_STEP3_LABEL if enabled else STEP_LABELS[2]
        )
        self._step_cards[3].visible = not enabled
        for ctrl in (self._step_card_texts[2], self._step_cards[3]):
            try:
                ctrl.update()
            except Exception:
                pass

    def refresh_combined_mode(self) -> None:
        """Re-read the setting and re-apply the merged / normal card layout.

        Called when the user returns to the 主頁 tab so a toggle made in the
        settings page is reflected without an app restart.
        """
        self.apply_combined_mode(self._read_combined_mode_setting())

    @staticmethod
    def _read_source_settings() -> tuple[str, str, str]:
        """Read download.source_mode and source privacy scopes from settings."""
        try:
            from app.core.settings_store import SettingsStore
            dl = SettingsStore(_settings_base_path()).get_section("download")
            return (
                str(dl.get("source_mode", "following") or "following"),
                str(dl.get("following_scope", "all") or "all"),
                str(dl.get("bookmark_scope", "all") or "all"),
            )
        except Exception:
            return "following", "all", "all"

    @staticmethod
    def _source_palette(mode: str) -> dict[str, str]:
        is_dark = False
        # Kept static for tests and because mode band colors are decorative.
        if mode == "bookmarks":
            return {
                "bg": ft.Colors.PINK_50,
                "border": ft.Colors.PINK_200,
                "accent": ft.Colors.PINK_700,
            }
        return {
            "bg": ft.Colors.BLUE_50,
            "border": ft.Colors.BLUE_200,
            "accent": ft.Colors.BLUE_700,
        }

    def apply_source_mode(self, mode: str, scope: str = "all") -> None:
        """Paint the main-page source-mode band and step labels."""
        mode = "bookmarks" if str(mode) == "bookmarks" else "following"
        scope = self._normalize_scope(scope)
        self._source_mode = mode
        self._active_scope = scope
        palette = self._source_palette(mode)
        self._mode_band.bgcolor = palette["bg"]
        self._mode_band.border = ft.Border.all(1, palette["border"])
        if mode == "bookmarks":
            self._bookmark_scope = scope
            self._mode_title.value = "目前模式：抓收藏"
            self._mode_subtitle.value = "步驟 2 會掃描你按過愛心的作品 PID，後續抓 URL / 下載流程不變。"
            self._scope_label.value = "收藏範圍"
            self._scope_row.visible = True
            self._step_card_texts[0].value = _BOOKMARK_STEP_LABELS[0]
            self._step_card_texts[1].value = _BOOKMARK_STEP_LABELS[1]
        else:
            self._following_scope = scope
            self._mode_title.value = "目前模式：抓追隨"
            self._mode_subtitle.value = "維持原本流程：抓追蹤畫師，再掃描畫師作品 PID。"
            self._scope_label.value = "追隨範圍"
            self._scope_row.visible = True
            self._step_card_texts[0].value = STEP_LABELS[0]
            self._step_card_texts[1].value = STEP_LABELS[1]
        self._btn_source_following = self._make_mode_button(
            "抓追隨", mode == "following", lambda e: self._on_source_mode_change("following")
        )
        self._btn_source_bookmarks = self._make_mode_button(
            "抓收藏", mode == "bookmarks", lambda e: self._on_source_mode_change("bookmarks")
        )
        self._btn_scope_public = self._make_mode_button(
            "公開", scope == "public", lambda e: self._on_scope_change("public")
        )
        self._btn_scope_private = self._make_mode_button(
            "非公開", scope == "private", lambda e: self._on_scope_change("private")
        )
        self._btn_scope_all = self._make_mode_button(
            "全部", scope == "all", lambda e: self._on_scope_change("all")
        )
        self._source_mode_controls.controls = [
            self._btn_source_following, self._btn_source_bookmarks,
        ]
        self._scope_row.controls = [
            self._scope_label,
            self._btn_scope_public,
            self._btn_scope_private,
            self._btn_scope_all,
        ]
        self._bookmark_scope_row = self._scope_row
        self._safe_update(
            self._mode_band, self._mode_title, self._mode_subtitle,
            self._source_mode_controls, self._scope_row, self._scope_label,
            self._step_card_texts[0], self._step_card_texts[1],
        )

    def _make_mode_button(self, text: str, active: bool, on_click):
        return glass_pill(
            text, current_theme(self._page), primary=active, on_click=on_click
        )

    @staticmethod
    def _normalize_scope(scope: str) -> str:
        scope = str(scope or "all")
        return scope if scope in {"public", "private", "all"} else "all"

    def refresh_source_mode(self) -> None:
        mode, following_scope, bookmark_scope = self._read_source_settings()
        self._following_scope = self._normalize_scope(following_scope)
        self._bookmark_scope = self._normalize_scope(bookmark_scope)
        active_scope = self._bookmark_scope if mode == "bookmarks" else self._following_scope
        self.apply_source_mode(mode, active_scope)

    def _persist_source_settings(self, fields: dict) -> None:
        with contextlib.suppress(Exception):
            from app.core.settings_store import SettingsStore
            SettingsStore(_settings_base_path()).update_fields("download", fields)

    def _on_source_mode_change(self, mode: str) -> None:
        self._persist_source_settings({"source_mode": mode})
        mode = "bookmarks" if str(mode) == "bookmarks" else "following"
        scope = self._bookmark_scope if mode == "bookmarks" else self._following_scope
        self.apply_source_mode(mode, scope)

    def _on_scope_change(self, scope: str) -> None:
        scope = self._normalize_scope(scope)
        key = "bookmark_scope" if self._source_mode == "bookmarks" else "following_scope"
        self._persist_source_settings({key: scope})
        self.apply_source_mode(self._source_mode, scope)

    def _on_bookmark_scope_change(self, scope: str) -> None:
        self._on_scope_change(scope)

    def _on_following_scope_change(self, scope: str) -> None:
        self._on_scope_change(scope)

    def append_log(self, html_line: str) -> None:
        self._log_panel.append_log(html_line)

    @staticmethod
    def _safe_update(*controls) -> None:
        """update() each control, swallowing detached-control errors.

        Detached controls (e.g. during build() before mount, or after a session
        GC) raise from update(); we swallow that so painting restored state is
        always safe.
        """
        for c in controls:
            with contextlib.suppress(Exception):
                c.update()

    def _make_progress_row(self, label: str):
        """Build one labeled progress row: [label] [expand bar] [count text].

        Returns ``(bar, text, row)``. Used for BOTH 整體進度 and 本作分頁 so the
        two bars are constructed identically (same geometry, same colors role).
        The row starts hidden — it is revealed on its first real update by
        :meth:`_render_progress_row`.
        """
        theme = current_theme(self._page)
        bar = ft.ProgressBar(
            value=0,
            expand=True,
            bar_height=_PROG_BAR_HEIGHT,
            border_radius=_PROG_BAR_RADIUS,
            color=theme.accent,
            bgcolor="#1FFFFFFF",
        )
        text = ft.Text(
            "",
            size=13,
            color=theme.text_primary,
            weight=ft.FontWeight.W_600,
            width=_PROG_TRAIL_W,
            text_align=ft.TextAlign.LEFT,
        )
        row = ft.Row(
            controls=[
                ft.Text(label, size=12, color=theme.text_secondary,
                        width=_PROG_LEAD_W),
                bar,
                text,
            ],
            spacing=_PROG_ROW_SPACING,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
        )
        return bar, text, row

    def _render_progress_row(self, row, bar, text, value: int, total: int,
                             trailing: str) -> None:
        """The ONE update path shared by 整體進度 and 本作分頁.

        Sets the bar fraction + trailing count, reveals (or hides) the row, then
        flushes it. Toggling ``visible`` False -> True on first data is the
        load-bearing fix: it forces Flet to (re)lay out the row with real
        content. An always-visible row first laid out with empty children
        renders degenerate and later ``.value`` patches never reflow it — the
        整體進度 freeze. 本作分頁 always worked because it was visible-gated; now
        both rows are, via this single method.
        """
        if total > 0:
            bar.value = max(0.0, min(1.0, value / total))
            text.value = trailing
            row.visible = True
        else:
            bar.value = 0
            text.value = ""
            row.visible = False
        self._safe_update(row)

    def _paint_progress(self, now: float | None = None) -> None:
        """Render overall progress into the bar + count via the shared path.

        Safe to call from build() before the controls are attached: the
        _safe_update inside _render_progress_row swallows the detached-control
        error, leaving the values painted for the imminent mount.
        """
        t = self._progress_total
        if t > 0:
            self._eta_text.value = self._format_eta(
                now if now is not None else time.monotonic()
            )
            trailing = f"{self._progress_value} / {t}"
        else:
            self._eta_text.value = ""
            trailing = ""
        self._render_progress_row(
            self._progress_row, self._progress_bar, self._progress_text,
            self._progress_value, t, trailing,
        )

    def update_progress(self, delta: int, total: int) -> None:
        # Workers emit (delta, total) per step, with delta == 0 marking a reset
        # at the start of a phase. We accumulate locally so the bar grows.
        try:
            d = int(delta)
            t = int(total)
        except (TypeError, ValueError):
            return
        now = time.monotonic()
        if d <= 0:
            self._progress_value = 0
            self._progress_started_at = now
        else:
            self._progress_value += d
            if self._progress_started_at is None:
                self._progress_started_at = now
        self._progress_total = t
        self._paint_progress(now)
        # _paint_progress already flushed the progress row; ETA lives on the
        # separate meta row, so flush that too.
        self._safe_update(self._meta_row)

    def update_page_progress(self, delta: int, total: int, pid: str = "") -> None:
        """Update the current PID's page progress without touching PID progress."""
        try:
            d = int(delta)
            t = int(total)
        except (TypeError, ValueError):
            return
        pid_text = "" if pid is None else str(pid)
        if t <= 0:
            self.clear_page_progress()
            return

        if pid_text != self._page_progress_pid:
            self._page_progress_value = 0
        if d <= 0:
            self._page_progress_value = 0
        else:
            self._page_progress_value += d

        self._page_progress_total = t
        self._page_progress_pid = pid_text
        self._page_progress_value = max(0, min(self._page_progress_value, t))
        label = f"PID {pid_text} 分頁" if pid_text else "分頁"
        trailing = f"{label} {self._page_progress_value}/{self._page_progress_total}"
        self._render_progress_row(
            self._page_progress_row, self._page_progress_bar,
            self._page_progress_text, self._page_progress_value, t, trailing,
        )

    def clear_page_progress(self) -> None:
        self._page_progress_value = 0
        self._page_progress_total = 0
        self._page_progress_pid = ""
        self._render_progress_row(
            self._page_progress_row, self._page_progress_bar,
            self._page_progress_text, 0, 0, "",
        )

    def _format_eta(self, now: float) -> str:
        if self._progress_started_at is None:
            return ""
        if self._progress_value <= 0 or self._progress_total <= 0:
            return ""
        if self._progress_value >= self._progress_total:
            return "預計剩餘：完成"
        elapsed = now - self._progress_started_at
        if elapsed <= 0:
            return ""
        remaining_items = self._progress_total - self._progress_value
        eta_sec = int(remaining_items * elapsed / self._progress_value)
        if eta_sec <= 0:
            return ""
        if eta_sec >= 3600:
            h, rem = divmod(eta_sec, 3600)
            m, s = divmod(rem, 60)
            return f"預計剩餘：{h}:{m:02d}:{s:02d}"
        m, s = divmod(eta_sec, 60)
        return f"預計剩餘：{m:02d}:{s:02d}"

    def update_countdown(self, remaining: int) -> None:
        try:
            r = int(remaining)
        except (TypeError, ValueError):
            r = 0
        self._countdown_text.value = f"倒數：{r} 秒" if r > 0 else ""
        # Update the meta Row, not just the Text, so it reflows reliably.
        self._safe_update(self._meta_row)

    def set_phase(self, text: str) -> None:
        """Update the phase indicator row below the progress bar."""
        has_text = bool(text and text.strip())
        self._phase_label.value = text if has_text else ""
        self._phase_row.visible = has_text
        try:
            self._phase_row.update()
        except Exception:
            pass

    def set_loading(self, busy: bool, message: str = "正在啟動...") -> None:
        """Show / hide the modal preparing overlay (dim + spinner + message)."""
        with self._loading_lock:
            if busy and not self._loading_open:
                self._loading_msg.value = message
                try:
                    self._page.show_dialog(self._loading_dialog)
                except Exception:
                    pass
                self._loading_open = True
            elif (not busy) and self._loading_open:
                try:
                    self._page.pop_dialog()
                except Exception:
                    pass
                self._loading_open = False
        try:
            self._page.update()
        except Exception:
            pass

    def set_running(self, is_running: bool) -> None:
        self._btn_pause.disabled = not is_running
        self._btn_stop.disabled = not is_running
        self._btn_run_all.disabled = is_running
        self._cards_disabled = is_running
        # Reset pause toggle to "暫停" whenever the worker stops or a fresh
        # run starts, otherwise the button could keep saying "繼續" with no
        # active worker to resume.
        if not is_running:
            self._is_paused = False
            self._btn_pause.content = "⏸ 暫停"
            self.set_phase("")
        else:
            self._is_paused = False
            self._btn_pause.content = "⏸ 暫停"

    def _on_run_all(self, e: ft.ControlEvent) -> None:
        if self._run_controller is None:
            return
        self._event_q.put(WorkerEvent("loading", (True, "正在啟動 一鍵執行...")))
        threading.Thread(
            target=self._run_in_background,
            args=(self._run_controller.run_all,),
            daemon=True,
        ).start()

    def _on_run_step(self, step: int) -> None:
        if self._run_controller is None:
            return
        if self._cards_disabled:
            return
        self._event_q.put(WorkerEvent("loading", (True, f"正在啟動 步驟 {step}...")))
        threading.Thread(
            target=self._run_in_background,
            args=(self._run_controller.run_step, step),
            daemon=True,
        ).start()

    def _run_in_background(self, fn, *args) -> None:
        """Run a RunController call off the UI thread so the loading overlay
        actually renders before the slow worker __init__ blocks the caller.
        Both loading toggles go through the queue so the actual show/pop_dialog
        happens on the event loop thread (where Flet patches actually flush)."""
        try:
            fn(*args)
        finally:
            self._event_q.put(WorkerEvent("loading", (False, "")))

    def _on_pause_toggle(self, e: ft.ControlEvent) -> None:
        t = self._active_thread
        if not t:
            return
        if self._is_paused:
            if hasattr(t, "resume"):
                try:
                    t.resume()
                except Exception:
                    pass
            self._is_paused = False
            self._btn_pause.content = "⏸ 暫停"
        else:
            if hasattr(t, "pause"):
                try:
                    t.pause()
                except Exception:
                    pass
            self._is_paused = True
            self._btn_pause.content = "▶ 繼續"
        try:
            self._btn_pause.update()
        except Exception:
            pass
        # Mirror pause state to the persistent UI snapshot so a post-GC
        # successor main() can restore the "▶ 繼續" button + paused worker
        # state instead of resetting the toggle to "暫停".
        try:
            self._event_q.put(WorkerEvent("pause_state", self._is_paused))
        except Exception:
            pass

    def _on_stop(self, e: ft.ControlEvent) -> None:
        t = self._active_thread
        if not (t and hasattr(t, "stop")):
            return
        # Disable stop button immediately so the user can't double-trigger;
        # show modal spinner until the worker finishes its finalize/cleanup
        # (writing pending PIDs, all_url snapshots, etc.).
        try:
            self._btn_stop.disabled = True
            self._btn_stop.update()
        except Exception:
            pass
        self._event_q.put(WorkerEvent("loading", (True, "正在停止，等待清理完成...")))
        try:
            t.stop()
        except Exception:
            pass
        threading.Thread(
            target=self._wait_for_stop, args=(t,), daemon=True,
        ).start()

    def _wait_for_stop(self, t) -> None:
        # Wait for the worker thread to actually terminate so finalize-on-stop
        # (atomic_write_text/json calls) is done before we release the UI.
        try:
            t.join(timeout=60)
        finally:
            self._event_q.put(WorkerEvent("loading", (False, "")))

    def build(self) -> ft.Column:
        self.refresh_source_mode()
        # Reflect 邊查邊下 in the initial card layout (merged 3+4 / step-4 hidden).
        self.apply_combined_mode(self._read_combined_mode_setting())
        # Paint any progress restored from a post-GC reattach so the bar shows
        # "116 / 320" immediately instead of waiting for the next worker event.
        self._paint_progress()
        theme = current_theme(self._page)
        top_row = ft.Row(
            controls=[
                self._btn_run_all,
                *self._step_cards,
                ft.Container(width=40),
                self._btn_pause,
                self._btn_stop,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            wrap=True,
        )
        control_area = glass_panel(
            ft.Column(
                controls=[
                    self._mode_band,
                    top_row,
                    self._progress_row,
                    self._page_progress_row,
                    self._meta_row,
                    self._phase_row,
                ],
                spacing=12,
            ),
            theme,
        )
        log_area = glass_panel(
            ft.Column(
                controls=[
                    ft.Text(
                        "即時 Log", size=12, weight=ft.FontWeight.BOLD,
                        color=theme.text_primary,
                    ),
                    self._log_panel.control,
                ],
                expand=True,
                spacing=8,
            ),
            theme,
            expand=True,
        )
        return ft.Column(
            controls=[control_area, log_area],
            expand=True,
            spacing=theme.gap,
        )
