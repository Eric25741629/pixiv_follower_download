from __future__ import annotations
import queue
import threading
import time
import flet as ft

from app.core.worker_event import WorkerEvent


STEP_LABELS = ["步驟 1\n抓追蹤", "步驟 2\n抓 PID", "步驟 3\n抓 URL", "步驟 4\n下載"]
_STATE_COLORS = {
    "idle":    ft.Colors.GREY_400,
    "running": ft.Colors.BLUE_600,
    "done":    ft.Colors.GREEN_600,
    "error":   ft.Colors.RED_600,
}
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
        self._step_cards = [self._make_step_card(i) for i in range(4)]

        self._btn_run_all = ft.FilledButton("▶ 一鍵執行", on_click=self._on_run_all)
        self._btn_step = [
            ft.OutlinedButton(f"步驟 {i+1}", on_click=lambda e, n=i+1: self._on_run_step(n))
            for i in range(4)
        ]
        self._btn_pause = ft.OutlinedButton("⏸ 暫停", on_click=self._on_pause_toggle, disabled=True)
        self._btn_stop = ft.OutlinedButton("⏹ 停止", on_click=self._on_stop, disabled=True)
        self._is_paused = False

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
        self._phase_label = ft.Text("", size=11, color=ft.Colors.BLUE_700, expand=True)
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
        self._log_list = ft.ListView(
            controls=self._log_lines,
            expand=True,
            spacing=1,
            auto_scroll=True,
        )

    def _make_step_card(self, index: int) -> ft.Card:
        container = ft.Container(
            content=ft.Text(
                STEP_LABELS[index],
                text_align=ft.TextAlign.CENTER,
                size=13,
            ),
            padding=12,
            bgcolor=_STATE_COLORS["idle"],
            border_radius=8,
            width=110,
            alignment=ft.Alignment(x=0, y=0),
        )
        self._step_card_containers.append(container)
        return ft.Card(content=container)

    def set_step_state(self, index: int, state: str) -> None:
        """Update step card color. state: 'idle'|'running'|'done'|'error'"""
        self._step_states[index] = state
        self._step_card_containers[index].bgcolor = _STATE_COLORS.get(state, _STATE_COLORS["idle"])

    def append_log(self, html_line: str) -> None:
        from app.gui.log_format import html_to_spans
        spans = html_to_spans(html_line)
        if not spans:
            return
        self._log_lines.append(ft.Text(spans=spans, size=12))
        if len(self._log_lines) > _MAX_LOG_LINES:
            self._log_lines.pop(0)

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
        # Defensive belt-and-suspenders: ensure the change is flushed even if
        # the dispatcher's batched page.update() is racing with this control's
        # own update from a background thread.
        try:
            self._page.update()
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
        for b in self._btn_step:
            b.disabled = is_running
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
        step_row = ft.Row(
            controls=self._step_cards,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
        )
        control_row = ft.Row(
            controls=[self._btn_run_all, *self._btn_step, self._btn_pause, self._btn_stop],
            wrap=True,
            spacing=8,
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
                step_row,
                control_row,
                progress_row,
                self._phase_row,
                ft.Divider(),
                ft.Text("即時 Log", size=12, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=self._log_list,
                    expand=True,
                    border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
                    border_radius=4,
                    padding=4,
                ),
            ],
            expand=True,
            spacing=12,
        )
