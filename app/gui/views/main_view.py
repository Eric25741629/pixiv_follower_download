from __future__ import annotations
import contextlib
import queue
import threading
import flet as ft

from app.core.worker_event import WorkerEvent
from app import i18n
from app.gui import components as c
from app.gui.glass import (
    current_theme,
    glass_dialog,
    glass_panel,
    glass_pill,
    set_pill_icon,
    set_pill_label,
    state_colors,
    style_pill,
)
from app.gui.log_panel import LogPanel
from app.gui.views.main_mode_row import (
    bookmark_step_labels,
    merged_step3_label,
    source_tooltips,
    _MainModeRowMixin,
    _settings_base_path,
    step_labels,
)
from app.gui.views.main_progress import _PROG_LEAD_W, _MainProgressMixin

# Label helpers moved to the mixin modules (file-size refactor) and re-exported
# above: step_labels() / merged_step3_label() etc. so call sites here
# (_make_step_card) and tests that ``from app.gui.views.main_view import
# step_labels`` keep working. They are i18n.t()-backed functions (resolved at
# call time, after the locale is set). The progress-bar geometry (_PROG_*) and
# ETA logic live in main_progress; source/scope/combined logic in main_mode_row.

__all__ = [
    "MainView",
    "step_labels",
    "bookmark_step_labels",
    "merged_step3_label",
    "source_tooltips",
    "_settings_base_path",
]


def _state_palette(page: ft.Page) -> dict[str, tuple[str, str]]:
    """Step-card state colors from the glass design system (call sites unchanged)."""
    return state_colors(current_theme(page))


class MainView(_MainProgressMixin, _MainModeRowMixin):
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

        # 控制鈕＝glass_pill 膠囊（與模式列同語彙），內嵌自繪 SVG 圖標
        # （play/pause/stop）。標籤改字走 set_pill_label、換圖標走
        # set_pill_icon（content 是 Row[Image, Text]，不可對 content 指派
        # 字串）；啟用/停用走 _set_pill_enabled（disabled + 半透明）。
        self._btn_run_all = self._make_action_pill(
            i18n.t("main.btn.run_all"), icon="play", primary=True, on_click=self._on_run_all,
        )
        self._btn_pause = self._make_action_pill(
            i18n.t("main.btn.pause"), icon="pause", on_click=self._on_pause_toggle, enabled=False,
        )
        self._btn_stop = self._make_action_pill(
            i18n.t("main.btn.stop"), icon="stop", on_click=self._on_stop, enabled=False,
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
        # 雙色：整體進度=accent、本作分頁=info — 兩條必須一眼可區分
        # （邊查邊下同時跑 PID 進度與單 PID 多頁進度）。
        self._progress_bar, self._progress_text, self._progress_row = (
            self._make_progress_row(i18n.t("main.progress.overall"), current_theme(page).accent)
        )
        self._page_progress_bar, self._page_progress_text, self._page_progress_row = (
            self._make_progress_row(i18n.t("main.progress.page"), current_theme(page).info)
        )
        self._page_progress_value = 0
        self._page_progress_total = 0
        self._page_progress_pid = ""

        # ── per-worker lanes (parallel 邊查邊下): one row per concurrent worker,
        # built dynamically from the lanes_init event; hidden otherwise. Scrolls
        # with a height cap (set in init_lanes) so a high worker count can't push
        # the log / pause-stop controls off-screen.
        self._lane_rows = {}
        self._lane_panel = ft.Column(controls=[], spacing=6, visible=False,
                                     scroll=ft.ScrollMode.AUTO)

        # ── meta line (正在下載 PID + ETA + cooldown countdown) ──────────────
        # 獨立於第二進度條：單頁作品會隱藏分頁條（t<=1），但「正在下載：PID」
        # 與倒數計時必須照常顯示，所以放在永遠可見的 meta 列上。
        self._downloading_text = ft.Text(
            "", size=12, color=current_theme(page).info,
            weight=ft.FontWeight.W_600,
        )
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
                self._downloading_text,
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
            i18n.t("main.loading.default"), size=15, weight=ft.FontWeight.BOLD,
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
                        i18n.t("main.loading.dont_close"), size=12,
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

        # ── 模式列：單一可換行膠囊列（取代舊的粉/藍實色帶） ──────────────────
        # 「來源」[抓追隨][抓收藏]・「範圍」[公開][非公開][全部]
        # 兩個群組各自 tight，外層 Row wrap=True：視窗縮小時整組換行，
        # 不會像舊 expand-slot 版那樣文字壓到按鈕。模式說明改為 pill tooltip。
        self._source_mode = "following"
        self._following_scope = "all"
        self._bookmark_scope = "all"
        self._active_scope = "all"
        self._source_label = ft.Text(
            i18n.t("main.source_label"), size=12, weight=ft.FontWeight.BOLD,
            color=current_theme(page).text_muted,
        )
        self._scope_label = ft.Text(
            i18n.t("main.scope_label.following"),
            size=12,
            weight=ft.FontWeight.BOLD,
            color=current_theme(page).text_secondary,
        )
        _tips = source_tooltips()
        self._btn_source_following = self._make_mode_button(
            i18n.t("main.source.following"), False, lambda e: self._on_source_mode_change("following"),
            tooltip=_tips["following"],
        )
        self._btn_source_bookmarks = self._make_mode_button(
            i18n.t("main.source.bookmarks"), False, lambda e: self._on_source_mode_change("bookmarks"),
            tooltip=_tips["bookmarks"],
        )
        self._btn_scope_public = self._make_mode_button(
            i18n.t("main.scope.public"), False, lambda e: self._on_scope_change("public")
        )
        self._btn_scope_private = self._make_mode_button(
            i18n.t("main.scope.private"), False, lambda e: self._on_scope_change("private")
        )
        self._btn_scope_all = self._make_mode_button(
            i18n.t("main.scope.all"), False, lambda e: self._on_scope_change("all")
        )
        self._scope_row = ft.Row(
            controls=[
                self._scope_label,
                self._btn_scope_public,
                self._btn_scope_private,
                self._btn_scope_all,
            ],
            spacing=6,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        # Backward-compatible alias for older tests / callers.
        self._bookmark_scope_row = self._scope_row
        self._source_mode_controls = ft.Row(
            controls=[self._btn_source_following, self._btn_source_bookmarks],
            spacing=6,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._source_group = ft.Row(
            controls=[self._source_label, self._source_mode_controls],
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._mode_row = ft.Row(
            controls=[self._source_group, self._scope_row],
            spacing=24,
            run_spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _make_action_pill(
        self, label: str, *, icon: str | None = None, primary: bool = False,
        on_click=None, enabled: bool = True,
    ) -> ft.Container:
        """glass_pill for the run/pause/stop controls.

        With ``icon`` the pill's ``content`` is a ``Row[Image, Text]``;
        change its label via :func:`set_pill_label`, its icon via
        :func:`set_pill_icon` (never assign a string to ``content``).
        """
        pill = glass_pill(
            label, current_theme(self._page), icon=icon, primary=primary,
            on_click=on_click,
        )
        self._set_pill_enabled(pill, enabled)
        return pill

    @staticmethod
    def _set_pill_enabled(pill: ft.Container, enabled: bool) -> None:
        """Toggle a glass pill's clickability + the dimmed disabled look."""
        pill.disabled = not enabled
        pill.opacity = 1.0 if enabled else 0.45

    def _make_step_card(self, index: int) -> ft.Container:
        theme = current_theme(self._page)
        palette = _state_palette(self._page)
        bg, fg = palette["idle"]
        text = ft.Text(
            step_labels()[index],
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
        theme = current_theme(self._page)
        palette = _state_palette(self._page)
        for i, state in enumerate(self._step_states):
            bg, fg = palette.get(state, palette["idle"])
            self._step_card_containers[i].bgcolor = bg
            self._step_card_texts[i].color = fg
        self._phase_label.color = theme.info
        # 進度條雙色 + meta 列 + 控制鈕/模式列膠囊就地重染。
        self._progress_bar.color = theme.accent
        self._page_progress_bar.color = theme.info
        self._downloading_text.color = theme.info
        self._eta_text.color = theme.text_secondary
        self._countdown_text.color = theme.warning
        style_pill(self._btn_run_all, theme, primary=True)
        style_pill(self._btn_pause, theme)
        style_pill(self._btn_stop, theme)
        self._source_label.color = theme.text_muted
        self._scope_label.color = theme.text_secondary
        # 模式 pills 由 apply_source_mode 以新 theme 重建。
        self.refresh_source_mode()
        self._safe_update(
            self._progress_row, self._page_progress_row, self._meta_row,
            self._btn_run_all, self._btn_pause, self._btn_stop,
            self._source_label,
        )
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

    # Source / scope / combined mode pills + persistence + step-card relabel
    # moved to main_mode_row._MainModeRowMixin (file-size refactor); inherited.

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

    # Dual progress-bar machinery (_make_progress_row / _render_progress_row /
    # _paint_progress / update_progress / update_page_progress /
    # _hide_page_progress_bar / clear_page_progress / _format_eta /
    # update_countdown) moved to main_progress._MainProgressMixin (file-size
    # refactor); inherited. _set_downloading_pid / _set_downloading_status stay
    # here (they own the meta-row status slot the mixin calls into).

    def _set_downloading_pid(self, pid_text: str) -> None:
        self._set_downloading_status(i18n.t("main.downloading_pid", pid=pid_text) if pid_text else "")

    def _set_downloading_status(self, value: str) -> None:
        """Single render slot for the current-PID status (查詢中/下載中)."""
        if self._downloading_text.value != value:
            self._downloading_text.value = value
            self._safe_update(self._meta_row)

    def set_phase(self, text: str) -> None:
        """Update the phase indicator row below the progress bar.

        「正在查詢/正在下載」 per-PID messages are routed to the meta row's
        正在下載 slot instead — the meta row (next to ETA/倒數) is where the
        current PID already shows, so rendering them in the phase row too
        would duplicate the same PID on two lines. Detection uses the localized
        prefixes (workers emit these via i18n.t), so it still works under en.
        """
        t = (text or "").strip()
        # Strip everything from the {pid} placeholder onward to get the prefix.
        q_pre = i18n.t("log.phase.querying", pid="\x00").split("\x00", 1)[0]
        d_pre = i18n.t("log.phase.downloading", pid="\x00").split("\x00", 1)[0]
        if (q_pre and t.startswith(q_pre)) or (d_pre and t.startswith(d_pre)):
            self._set_downloading_status(t)
            return
        has_text = bool(t)
        self._phase_label.value = text if has_text else ""
        self._phase_row.visible = has_text
        try:
            self._phase_row.update()
        except Exception:
            pass

    def set_loading(self, busy: bool, message: str = "") -> None:
        """Show / hide the modal preparing overlay (dim + spinner + message)."""
        with self._loading_lock:
            if busy and not self._loading_open:
                self._loading_msg.value = message or i18n.t("main.loading.default")
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
        # show_dialog/pop_dialog 引發的整頁重排會把 log 捲回最上方（按下停止
        # 時最明顯）— 跟隨中就排程跳回底部，且 pending 旗標會吃掉重排產生的
        # 「離底 END」事件，避免跟隨被誤關。
        self._log_panel.notify_relayout()

    def set_running(self, is_running: bool) -> None:
        self._set_pill_enabled(self._btn_pause, is_running)
        self._set_pill_enabled(self._btn_stop, is_running)
        self._set_pill_enabled(self._btn_run_all, not is_running)
        self._cards_disabled = is_running
        # Reset pause toggle to "暫停" whenever the worker stops or a fresh
        # run starts, otherwise the button could keep saying "繼續" with no
        # active worker to resume.
        self._is_paused = False
        set_pill_label(self._btn_pause, i18n.t("main.btn.pause"))
        set_pill_icon(self._btn_pause, "pause")
        if not is_running:
            self.set_phase("")

    def _on_run_all(self, e: ft.ControlEvent) -> None:
        if self._run_controller is None:
            return
        self._event_q.put(WorkerEvent("loading", (True, i18n.t("main.loading.run_all"))))
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
        self._event_q.put(WorkerEvent("loading", (True, i18n.t("main.loading.step", step=step))))
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
            set_pill_label(self._btn_pause, i18n.t("main.btn.pause"))
            set_pill_icon(self._btn_pause, "pause")
        else:
            if hasattr(t, "pause"):
                try:
                    t.pause()
                except Exception:
                    pass
            self._is_paused = True
            set_pill_label(self._btn_pause, i18n.t("main.btn.resume"))
            set_pill_icon(self._btn_pause, "play")
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
            self._set_pill_enabled(self._btn_stop, False)
            self._btn_stop.update()
        except Exception:
            pass
        self._event_q.put(WorkerEvent("loading", (True, i18n.t("main.loading.stopping"))))
        try:
            t.stop()
        except Exception:
            pass
        threading.Thread(
            target=self._wait_for_stop, args=(t,), daemon=True,
        ).start()

    def _wait_for_stop(self, t) -> None:
        # Wait for the worker thread to actually terminate so finalize-on-stop
        # (atomic_write_text/json calls, JXL conversion backlog) is done
        # before we release the UI.  No timeout: the spinner must stay up
        # until every queued JXL conversion has finished (the worker updates
        # the spinner text with the remaining count while it drains).
        try:
            t.join()
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
                    self._mode_row,
                    top_row,
                    self._progress_row,
                    self._page_progress_row,
                    self._lane_panel,
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
                    c.subhead(theme, i18n.t("main.log_title")),
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
