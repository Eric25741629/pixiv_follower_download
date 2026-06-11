from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import flet as ft

from app.core.stats_collector import StatsCollector
from app.gui.glass import current_theme, glass_panel

_MAX_BAR_PX = 300

# Bar-track fill behind each bar (low-alpha white, reads on both themes).
_BAR_TRACK_BG = "#1FFFFFFF"


def _accent_cycle(page: ft.Page) -> list[str]:
    """Theme accent-family colors cycled by stat cards and chart bars."""
    t = current_theme(page)
    return [t.accent, t.info, t.success, t.warning]


class StatsView:
    """Statistics view with overview cards and per-cookie bar chart."""

    def __init__(self, page: ft.Page, stats_collector: StatsCollector) -> None:
        self._page = page
        self._stats = stats_collector
        self._mode = "session"
        self._refresh_task: asyncio.Task | None = None

        self._card_values: list[ft.Text] = [
            ft.Text("0", size=20, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER),
            ft.Text("0", size=20, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER),
            ft.Text("0", size=20, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER),
            ft.Text("0:00:00", size=20, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER),
        ]

        self._chart_container = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO)

        # Flet 0.84's Tabs requires TabBar+TabBarView with separate content
        # bodies. Both modes here share one body, so a plain button toggle
        # is cleaner than forcing the new Tabs structure.
        self._btn_session = ft.FilledButton(
            "本次執行", on_click=lambda e: self._switch_mode("session"),
        )
        self._btn_lifetime = ft.OutlinedButton(
            "歷史累計", on_click=lambda e: self._switch_mode("lifetime"),
        )
        self._mode_toggle = ft.Row(
            controls=[self._btn_session, self._btn_lifetime], spacing=8,
        )

    def _switch_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        is_session = mode == "session"
        # Swap which side is FilledButton (active). Flet 0.84 doesn't let
        # us morph button types, so we just toggle them as filled/outlined
        # via Row controls reassignment.
        self._btn_session = ft.FilledButton(
            "本次執行", on_click=lambda e: self._switch_mode("session"),
        ) if is_session else ft.OutlinedButton(
            "本次執行", on_click=lambda e: self._switch_mode("session"),
        )
        self._btn_lifetime = ft.OutlinedButton(
            "歷史累計", on_click=lambda e: self._switch_mode("lifetime"),
        ) if is_session else ft.FilledButton(
            "歷史累計", on_click=lambda e: self._switch_mode("lifetime"),
        )
        self._mode_toggle.controls = [self._btn_session, self._btn_lifetime]
        try:
            self._mode_toggle.update()
        except Exception:
            pass
        self._refresh_now()

    def build(self) -> ft.Container:
        cards = self._build_cards()
        chart_section = ft.Column(
            controls=[
                ft.Text("Cookie 請求次數", size=14, weight=ft.FontWeight.BOLD),
                self._chart_container,
            ],
            spacing=8,
        )
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("統計資料", size=20, weight=ft.FontWeight.BOLD),
                    self._mode_toggle,
                    cards,
                    ft.Divider(),
                    chart_section,
                ],
                spacing=16,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=16,
            expand=True,
        )

    def _build_cards(self) -> ft.Row:
        labels = ["總下載流量", "JXL 節省空間", "HTTP 請求數", "執行時間"]
        theme = current_theme(self._page)
        value_colors = _accent_cycle(self._page)
        self._card_containers: list[ft.Container] = []
        self._card_labels: list[ft.Text] = []
        cards: list[ft.Container] = []
        for i, label in enumerate(labels):
            label_text = ft.Text(
                label, size=12, color=theme.text_secondary, text_align=ft.TextAlign.CENTER,
            )
            self._card_values[i].color = value_colors[i % len(value_colors)]
            panel = glass_panel(
                ft.Column(
                    controls=[label_text, self._card_values[i]],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                ),
                theme,
                padding=16,
                radius=theme.radius_sm,
                width=160,
            )
            self._card_containers.append(panel)
            self._card_labels.append(label_text)
            cards.append(panel)
        return ft.Row(controls=cards, wrap=True, spacing=12)

    def start_auto_refresh(self) -> None:
        if self._refresh_task is not None:
            return
        self._refresh_task = self._page.run_task(self._auto_refresh_loop)

    def stop_auto_refresh(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None

    async def _auto_refresh_loop(self) -> None:
        try:
            while True:
                self._refresh_now()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    def _refresh_now(self) -> None:
        try:
            if self._mode == "session":
                data = self._stats.get_session_stats()
                self._update_session_cards(data)
                self._update_chart(data.get("requests", {}))
            else:
                data = self._stats.get_lifetime_stats()
                self._update_lifetime_cards(data)
                self._update_chart(data.get("cookie_request_counts", {}))
        except Exception:
            pass

    def _update_session_cards(self, data: dict[str, Any]) -> None:
        self._card_values[0].value = StatsCollector.format_bytes(data.get("bytes", 0))
        jxl_src = data.get("jxl_src", 0)
        jxl_dst = data.get("jxl_dst", 0)
        saved = jxl_src - jxl_dst if jxl_src > jxl_dst else 0
        self._card_values[1].value = StatsCollector.format_bytes(saved)
        total_req = sum(data.get("requests", {}).values())
        self._card_values[2].value = str(total_req)
        self._card_values[3].value = StatsCollector.format_elapsed(data.get("elapsed", 0.0))
        for t in self._card_values:
            try:
                t.update()
            except Exception:
                pass

    def _update_lifetime_cards(self, data: dict[str, Any]) -> None:
        self._card_values[0].value = StatsCollector.format_bytes(
            data.get("lifetime_bytes_downloaded", 0)
        )
        jxl_src = data.get("lifetime_jxl_src", 0)
        jxl_dst = data.get("lifetime_jxl_dst", 0)
        saved = jxl_src - jxl_dst if jxl_src > jxl_dst else 0
        self._card_values[1].value = StatsCollector.format_bytes(saved) if saved > 0 else "-"
        cookie_counts = data.get("cookie_request_counts", {})
        total_req = sum(cookie_counts.values())
        self._card_values[2].value = str(total_req)
        self._card_values[3].value = str(data.get("lifetime_sessions", 0)) + " 次"
        for t in self._card_values:
            try:
                t.update()
            except Exception:
                pass

    def _safe_chart_update(self) -> None:
        with contextlib.suppress(Exception):
            self._chart_container.update()

    def _build_cookie_bar_rows(self, cookie_requests: dict[str, int]) -> list[ft.Control]:
        if not cookie_requests:
            return []
        sorted_items = sorted(cookie_requests.items(), key=lambda kv: kv[1], reverse=True)
        max_count = max(1, sorted_items[0][1])
        palette = _accent_cycle(self._page)
        rows: list[ft.Control] = []
        for idx, (label, count) in enumerate(sorted_items):
            bar_w = max(2, int(count / max_count * _MAX_BAR_PX))
            color = palette[idx % len(palette)]
            bar = ft.Container(
                content=ft.Container(bgcolor=color, width=bar_w, height=18, border_radius=4),
                width=_MAX_BAR_PX, height=18, bgcolor=_BAR_TRACK_BG, border_radius=4,
                alignment=ft.Alignment(x=-1, y=0),
            )
            rows.append(
                ft.Row(
                    controls=[
                        ft.Text(label, size=11, width=120, overflow=ft.TextOverflow.ELLIPSIS, no_wrap=True),
                        bar,
                        ft.Text(str(count), size=11, width=60),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        return rows

    def _update_chart(self, requests: dict[str, int]) -> None:
        theme = current_theme(self._page)
        if not requests:
            self._chart_container.controls = [
                ft.Text("尚無資料", size=12, color=theme.text_muted)
            ]
            self._safe_chart_update()
            return

        free_count = requests.get("免Cookie", 0)
        cookie_requests = {k: v for k, v in requests.items() if k != "免Cookie"}
        rows = self._build_cookie_bar_rows(cookie_requests)
        if free_count > 0:
            rows.append(ft.Text(
                f"免 Cookie：{free_count} 次", size=12, color=theme.info,
            ))
        if not rows:
            rows = [ft.Text("尚無資料", size=12, color=theme.text_muted)]

        self._chart_container.controls = rows
        self._safe_chart_update()

    def refresh_theme(self) -> None:
        """Re-apply theme-dependent colors after a light/dark toggle."""
        theme = current_theme(self._page)
        value_colors = _accent_cycle(self._page)
        for container in getattr(self, "_card_containers", []) or []:
            container.bgcolor = theme.panel_bg if theme.blur_enabled else theme.panel_bg_opaque
            container.border = ft.border.all(1, theme.panel_border)
            try:
                container.update()
            except Exception:
                pass
        for label in getattr(self, "_card_labels", []) or []:
            label.color = theme.text_secondary
            try:
                label.update()
            except Exception:
                pass
        for i, value in enumerate(self._card_values):
            value.color = value_colors[i % len(value_colors)]
            try:
                value.update()
            except Exception:
                pass
        # Re-render the chart so bar colors and any inline text picks up
        # the new palette without waiting for the next 1 s tick.
        self._refresh_now()
