from __future__ import annotations
import queue
import threading
import flet as ft


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
        self._btn_pause = ft.OutlinedButton("⏸ 暫停", on_click=self._on_pause, disabled=True)
        self._btn_stop = ft.OutlinedButton("⏹ 停止", on_click=self._on_stop, disabled=True)

        self._progress_bar = ft.ProgressBar(value=0, expand=True)
        self._progress_text = ft.Text("", size=12, color=ft.Colors.GREY_600, width=120)
        self._countdown_text = ft.Text(
            "",
            size=13,
            color=ft.Colors.ORANGE_600,
            weight=ft.FontWeight.BOLD,
            width=140,
        )
        self._progress_value = 0
        self._progress_total = 0

        # Modal "preparing..." overlay shown while a step is being launched.
        self._loading_msg = ft.Text("正在啟動...", size=15, weight=ft.FontWeight.BOLD)
        self._loading_dialog = ft.AlertDialog(
            modal=True,
            content=ft.Column(
                controls=[
                    ft.ProgressRing(width=56, height=56, stroke_width=4),
                    self._loading_msg,
                    ft.Text("請稍候，前置作業進行中...", size=12, color=ft.Colors.GREY_600),
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
        if d <= 0:
            self._progress_value = 0
        else:
            self._progress_value += d
        self._progress_total = t
        if t > 0:
            ratio = self._progress_value / t
            self._progress_bar.value = max(0.0, min(1.0, ratio))
            self._progress_text.value = f"{self._progress_value}/{t}"
        else:
            self._progress_bar.value = 0
            self._progress_text.value = ""
        try:
            self._progress_bar.update()
            self._progress_text.update()
        except Exception:
            pass

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

    def _on_run_all(self, e: ft.ControlEvent) -> None:
        if self._run_controller is None:
            return
        self.set_loading(True, "正在啟動 一鍵執行...")
        threading.Thread(
            target=self._run_in_background,
            args=(self._run_controller.run_all,),
            daemon=True,
        ).start()

    def _on_run_step(self, step: int) -> None:
        if self._run_controller is None:
            return
        self.set_loading(True, f"正在啟動 步驟 {step}...")
        threading.Thread(
            target=self._run_in_background,
            args=(self._run_controller.run_step, step),
            daemon=True,
        ).start()

    def _run_in_background(self, fn, *args) -> None:
        """Run a RunController call off the UI thread so the loading overlay
        actually renders before the slow worker __init__ blocks the caller."""
        try:
            fn(*args)
        finally:
            self.set_loading(False)

    def _on_pause(self, e: ft.ControlEvent) -> None:
        if self._active_thread and hasattr(self._active_thread, "pause"):
            self._active_thread.pause()

    def _on_stop(self, e: ft.ControlEvent) -> None:
        if self._active_thread and hasattr(self._active_thread, "stop"):
            self._active_thread.stop()

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
            controls=[self._progress_bar, self._progress_text, self._countdown_text],
            spacing=12,
        )
        return ft.Column(
            controls=[
                step_row,
                control_row,
                progress_row,
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
