from __future__ import annotations
import asyncio
import queue
import threading
import time
import flet as ft

from app.core.worker_event import WorkerEvent


STEP_LABELS = ["步驟 1\n抓追蹤", "步驟 2\n抓 PID", "步驟 3\n抓 URL", "步驟 4\n下載"]

# Two palettes — light uses saturated 600-level bg + white text; dark uses
# softer 400/800-level bg + dark text so colors don't look neon on black.
_STATE_COLORS_LIGHT: dict[str, tuple[str, str]] = {
    "idle":    (ft.Colors.GREY_300,  ft.Colors.BLACK),
    "running": (ft.Colors.BLUE_600,  ft.Colors.WHITE),
    "done":    (ft.Colors.GREEN_700, ft.Colors.WHITE),
    "error":   (ft.Colors.RED_600,   ft.Colors.WHITE),
}
_STATE_COLORS_DARK: dict[str, tuple[str, str]] = {
    "idle":    (ft.Colors.GREY_800,         ft.Colors.GREY_300),
    "running": (ft.Colors.BLUE_400,         ft.Colors.BLACK),
    "done":    (ft.Colors.TEAL_400,         ft.Colors.BLACK),
    "error":   (ft.Colors.DEEP_ORANGE_400,  ft.Colors.BLACK),
}


def _is_dark_mode(page: ft.Page) -> bool:
    mode = getattr(page, "theme_mode", None)
    if mode == ft.ThemeMode.DARK:
        return True
    if mode == ft.ThemeMode.LIGHT:
        return False
    try:
        return page.platform_brightness == ft.Brightness.DARK
    except Exception:
        return False


def _state_palette(page: ft.Page) -> dict[str, tuple[str, str]]:
    return _STATE_COLORS_DARK if _is_dark_mode(page) else _STATE_COLORS_LIGHT


_MAX_LOG_LINES = 2000


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

        self._btn_run_all = ft.FilledButton("▶ 一鍵執行", on_click=self._on_run_all)
        self._btn_pause = ft.OutlinedButton("⏸ 暫停", on_click=self._on_pause_toggle, disabled=True)
        self._btn_stop = ft.OutlinedButton("⏹ 停止", on_click=self._on_stop, disabled=True)
        self._is_paused = False
        self._cards_disabled = False

        self._progress_bar = ft.ProgressBar(value=0, expand=True)
        self._progress_text = ft.Text("", size=12, color=ft.Colors.GREY_600, width=120)
        self._eta_text = ft.Text(
            "",
            size=12,
            color=ft.Colors.BLUE_GREY_500,
            width=160,
        )
        self._countdown_text = ft.Text(
            "",
            size=13,
            color=ft.Colors.ORANGE_600,
            weight=ft.FontWeight.BOLD,
            width=140,
        )
        self._progress_value = 0
        self._progress_total = 0
        self._progress_started_at: float | None = None

        self._phase_ring = ft.ProgressRing(width=14, height=14, stroke_width=2)
        self._phase_label = ft.Text(
            "",
            size=11,
            color=ft.Colors.BLUE_300 if _is_dark_mode(page) else ft.Colors.BLUE_700,
            expand=True,
        )
        self._phase_row = ft.Row(
            controls=[self._phase_ring, self._phase_label],
            spacing=6,
            visible=False,
        )

        # Modal overlay shown while a step is launching or stopping.
        self._loading_msg = ft.Text("正在啟動...", size=15, weight=ft.FontWeight.BOLD)
        self._loading_dialog = ft.AlertDialog(
            modal=True,
            content=ft.Column(
                controls=[
                    ft.ProgressRing(width=56, height=56, stroke_width=4),
                    self._loading_msg,
                    ft.Text("請勿關閉視窗", size=12, color=ft.Colors.GREY_600),
                ],
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=14,
            ),
        )
        self._loading_open = False
        self._loading_lock = threading.Lock()

        self._log_lines: list[ft.Text] = []
        self._auto_scroll_enabled = True
        self._scroll_pending = False
        self._last_scroll_pixels: float | None = None
        self._last_max_scroll_extent: float | None = None
        # auto_scroll stays False forever — we manually call scroll_to(offset=-1)
        # via _schedule_scroll_to_bottom().  Toggling auto_scroll at runtime
        # rebuilds Flutter's ScrollController and breaks mouse-wheel scrolling
        # mid-session.  build_controls_on_demand=False is required for
        # scroll_to to work on a ListView (the Flet docstring explicitly says
        # scroll_to is ineffective when items are built lazily).
        self._log_list = ft.ListView(
            controls=self._log_lines,
            expand=True,
            spacing=1,
            auto_scroll=False,
            build_controls_on_demand=False,
            on_scroll=self._on_log_scroll,
            scroll_interval=200,
        )
        # The pill overlay used to be an expand=True Container with alignment,
        # but that wrapped a transparent Container over the full Stack area —
        # Flet's Container always supports on_hover so Flutter wraps it in a
        # MouseRegion that absorbs pointer events (including mouse wheel and
        # click), blocking the ListView underneath AND the inner pill.
        # Solution: use a Row positioned only along the bottom strip via
        # Stack positioning (left/right/bottom).  Row doesn't paint nor hit
        # test its empty space, so wheel events above the strip reach the
        # ListView, and clicks on either side of the pill pass through too.
        self._pill_overlay = ft.Row(
            [
                ft.Container(
                    content=ft.Text("↓ 跳到最新", size=11, color=ft.Colors.WHITE),
                    bgcolor=ft.Colors.with_opacity(0.75, ft.Colors.BLUE_GREY_700),
                    border_radius=20,
                    padding=ft.padding.symmetric(horizontal=16, vertical=7),
                    on_click=self._on_scroll_to_bottom,
                    ink=True,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            left=0,
            right=0,
            bottom=15,
            visible=False,
        )

    def _make_step_card(self, index: int) -> ft.Card:
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
            border_radius=8,
            width=110,
            alignment=ft.Alignment(x=0, y=0),
            ink=True,
            on_click=lambda e, n=index + 1: self._on_run_step(n),
        )
        self._step_card_containers.append(container)
        self._step_card_texts.append(text)
        return ft.Card(content=container)

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
        ``_is_dark_mode(page)`` rather than auto-themed Flet components.
        Best-effort: swallows ``update()`` errors on detached controls.
        """
        palette = _state_palette(self._page)
        for i, state in enumerate(self._step_states):
            bg, fg = palette.get(state, palette["idle"])
            self._step_card_containers[i].bgcolor = bg
            self._step_card_texts[i].color = fg
        self._phase_label.color = (
            ft.Colors.BLUE_300 if _is_dark_mode(self._page) else ft.Colors.BLUE_700
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

    def append_log(self, html_line: str) -> None:
        from app.gui.log_format import html_to_spans
        spans = html_to_spans(html_line)
        if not spans:
            return
        self._log_lines.append(ft.Text(spans=spans, size=12))
        if len(self._log_lines) > _MAX_LOG_LINES:
            self._log_lines.pop(0)
        # Only schedule a scroll_to when the ListView is still attached to a
        # live page tree. After a nav-rail switch the MainView is detached
        # (its subtree was replaced inside content_area); calling scroll_to
        # then sends an _invoke_method round-trip to a widget the client no
        # longer renders, which piles awaited tasks on the event loop and
        # makes the rapid-tab-switch session-GC scenario worse. Re-attach
        # happens transparently on the next log line after the user returns
        # to MainView.
        # If the last known scroll position was at/near the bottom, treat
        # new lines as wanting to follow — appending shifts max_scroll_extent
        # so the user can be at the visual bottom while extent_after silently
        # grows, leaving the pill stuck even though they're already there.
        if (not self._auto_scroll_enabled
                and self._last_scroll_pixels is not None
                and self._last_max_scroll_extent is not None
                and self._last_max_scroll_extent - self._last_scroll_pixels <= 30):
            self._auto_scroll_enabled = True
            self._pill_overlay.visible = False
        if self._auto_scroll_enabled and getattr(self._log_list, "page", None) is not None:
            self._schedule_scroll_to_bottom()

    def _schedule_scroll_to_bottom(self) -> None:
        """Coalesce scroll requests within one event-loop tick — many
        log lines can land in the same dispatcher poll, but we only need
        one scroll_to per render."""
        if self._scroll_pending:
            return
        self._scroll_pending = True
        try:
            asyncio.create_task(self._do_scroll_to_bottom())
        except RuntimeError:
            # No running loop (called outside event loop) — drop quietly.
            self._scroll_pending = False

    async def _do_scroll_to_bottom(self) -> None:
        try:
            await self._log_list.scroll_to(offset=-1, duration=0)
        except Exception:
            pass
        finally:
            self._scroll_pending = False

    def _on_log_scroll(self, e: ft.OnScrollEvent) -> None:
        # Don't trust ScrollType.USER + direction for wheel events — Flutter's
        # pointerScroll path does not always fire UserScrollNotification, and
        # when it does the direction enum value is unreliable across platforms.
        # Use UPDATE events with pixel-delta tracking instead.
        if e.event_type != ft.ScrollType.UPDATE:
            return
        prev = self._last_scroll_pixels
        self._last_scroll_pixels = e.pixels
        self._last_max_scroll_extent = e.max_scroll_extent
        if self._auto_scroll_enabled:
            # User scrolled up if pixels decreased by >10px AND we're now
            # more than 30px from the bottom.  The pixel-delta filter rules
            # out the spurious decreases caused by pop(0) when the log hits
            # _MAX_LOG_LINES (those shift content up but stay near bottom).
            if prev is not None and e.pixels < prev - 10 and e.extent_after > 30:
                self._auto_scroll_enabled = False
                self._pill_overlay.visible = True
        else:
            # Re-enable when the user is near the bottom. The previous
            # ≤20 threshold was tight enough that scroll_interval=200
            # throttling could leave the final event at extent_after≈25,
            # stranding the pill visible at the visual bottom. A scroll-down
            # nudge that lands ≤80px from the edge also counts as intent.
            scrolling_down = prev is not None and e.pixels > prev
            if e.extent_after <= 50 or (scrolling_down and e.extent_after <= 80):
                self._enable_auto_scroll()

    def _enable_auto_scroll(self) -> None:
        self._auto_scroll_enabled = True
        self._pill_overlay.visible = False
        # Actively jump to bottom — setting a flag alone won't move the
        # viewport until the next log line arrives.
        self._schedule_scroll_to_bottom()

    def _on_scroll_to_bottom(self, e: ft.ControlEvent) -> None:
        self._enable_auto_scroll()

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
        if t > 0:
            ratio = self._progress_value / t
            self._progress_bar.value = max(0.0, min(1.0, ratio))
            self._progress_text.value = f"{self._progress_value}/{t}"
            self._eta_text.value = self._format_eta(now)
        else:
            self._progress_bar.value = 0
            self._progress_text.value = ""
            self._eta_text.value = ""
        try:
            self._progress_bar.update()
            self._progress_text.update()
            self._eta_text.update()
        except Exception:
            pass

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
        try:
            self._countdown_text.update()
        except Exception:
            pass

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
        progress_row = ft.Row(
            controls=[
                self._progress_bar,
                self._progress_text,
                self._eta_text,
                self._countdown_text,
            ],
            spacing=12,
        )
        return ft.Column(
            controls=[
                top_row,
                progress_row,
                self._phase_row,
                ft.Divider(),
                ft.Text("即時 Log", size=12, weight=ft.FontWeight.BOLD),
                ft.Stack(
                    controls=[
                        ft.SelectionArea(
                            content=ft.Container(
                                content=self._log_list,
                                expand=True,
                                border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
                                border_radius=4,
                                padding=4,
                            ),
                        ),
                        self._pill_overlay,
                    ],
                    expand=True,
                    fit=ft.StackFit.EXPAND,
                ),
            ],
            expand=True,
            spacing=12,
        )
