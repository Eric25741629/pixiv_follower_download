from __future__ import annotations

import asyncio
from typing import Any

import flet as ft

from app.core.stats_collector import StatsCollector

_CARD_COLORS = [
    ft.Colors.BLUE_100,
    ft.Colors.GREEN_100,
    ft.Colors.ORANGE_100,
    ft.Colors.PURPLE_100,
]

_BAR_COLORS = [
    ft.Colors.BLUE_400,
    ft.Colors.GREEN_400,
    ft.Colors.ORANGE_400,
    ft.Colors.PURPLE_400,
    ft.Colors.RED_400,
    ft.Colors.TEAL_400,
    ft.Colors.PINK_400,
    ft.Colors.AMBER_400,
]

_MAX_BAR_PX = 300


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

        self._segmented = ft.SegmentedButton(
            selected={"session"},
            on_change=self._on_mode_change,
            segments=[
                ft.Segment(value="session", label=ft.Text("本次執行")),
                ft.Segment(value="lifetime", label=ft.Text("歷史累計")),
            ],
        )

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
                    self._segmented,
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
        cards: list[ft.Card] = []
        for i, label in enumerate(labels):
            card = ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(label, size=12, color=ft.Colors.GREY_700, text_align=ft.TextAlign.CENTER),
                            self._card_values[i],
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=4,
                    ),
                    padding=16,
                    bgcolor=_CARD_COLORS[i],
                    border_radius=8,
                    width=160,
                    alignment=ft.Alignment(x=0, y=0),
                ),
            )
            cards.append(card)
        return ft.Row(controls=cards, wrap=True, spacing=12)

    def _on_mode_change(self, e: ft.ControlEvent) -> None:
        selected = e.control.selected
        if selected:
            self._mode = next(iter(selected))
        self._refresh_now()

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
        if self._mode == "session":
            data = self._stats.get_session_stats()
            self._update_session_cards(data)
            self._update_chart(data.get("requests", {}))
        else:
            data = self._stats.get_lifetime_stats()
            self._update_lifetime_cards(data)
            self._update_chart(data.get("cookie_request_counts", {}))

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
        self._card_values[1].value = "-"
        cookie_counts = data.get("cookie_request_counts", {})
        total_req = sum(cookie_counts.values())
        self._card_values[2].value = str(total_req)
        self._card_values[3].value = str(data.get("lifetime_sessions", 0)) + " 次"
        for t in self._card_values:
            try:
                t.update()
            except Exception:
                pass

    def _update_chart(self, requests: dict[str, int]) -> None:
        if not requests:
            self._chart_container.controls = [ft.Text("尚無資料", size=12, color=ft.Colors.GREY_500)]
            try:
                self._chart_container.update()
            except Exception:
                pass
            return

        sorted_items = sorted(requests.items(), key=lambda kv: kv[1], reverse=True)
        max_count = sorted_items[0][1] if sorted_items else 1
        if max_count <= 0:
            max_count = 1

        rows: list[ft.Row] = []
        for idx, (label, count) in enumerate(sorted_items):
            bar_w = max(2, int(count / max_count * _MAX_BAR_PX))
            color = _BAR_COLORS[idx % len(_BAR_COLORS)]
            rows.append(
                ft.Row(
                    controls=[
                        ft.Text(label, size=11, width=120, overflow=ft.TextOverflow.ELLIPSIS, no_wrap=True),
                        ft.Container(
                            bgcolor=color,
                            width=bar_w,
                            height=18,
                            border_radius=4,
                        ),
                        ft.Text(str(count), size=11, width=60),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )

        self._chart_container.controls = rows
        try:
            self._chart_container.update()
        except Exception:
            pass
