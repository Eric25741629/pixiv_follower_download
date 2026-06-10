"""即時 Log 面板：單一 selectable Text + 意圖驅動的「跳到最新」狀態機。

設計文件: docs/superpowers/specs/2026-06-10-log-follow-rearchitecture-design.md

跨行複製: 所有 log 行的 spans 串在同一個 ft.Text(selectable=True) 裡
（行尾接 "\\n" span），單一控件內的跨行框選 + Ctrl+C 是 Flutter 原生行為。
多個獨立 Text 包在 SelectionArea 底下無法跨控件選取（實測無效），故不採用。

跟隨狀態機（單一狀態 _following；膠囊可見性恆等於 not _following）:
  - 滾輪向上（GestureDetector.on_scroll, scroll_delta.y < 0）→ 停止跟隨。
    這是 100% 確定的使用者意圖，不需要任何像素差啟發式。
  - ListView ScrollType.END（滾動靜止的定案位置）貼底 → 恢復跟隨。
    END 不受 UPDATE 節流漏事件影響，解決「滾到底卻沒恢復」。
  - END 離底且沒有程式捲動排程中 → 停止跟隨（涵蓋拖捲軸條上滾）。
    程式 scroll_to(duration=0) 的 END 必落在底部，天然走恢復分支。
  - 膠囊點擊 → 恢復跟隨並跳到底。
"""
from __future__ import annotations

import asyncio
import contextlib

import flet as ft

from app.gui.log_format import html_to_spans

_MAX_LOG_LINES = 2000
# END 事件 extent_after 在這個距離內視為「貼底」。
_BOTTOM_EPS = 8.0


class LogPanel:
    def __init__(self, *, max_lines: int = _MAX_LOG_LINES):
        self._max_lines = max_lines
        self._following = True
        self._scroll_pending = False
        # 每行佔用的 span 數（含行尾 "\n"），裁切時從頭移除整行區段。
        self._line_span_counts: list[int] = []

        self._text = ft.Text(spans=[], size=12, selectable=True)
        # ListView 只是滾動容器（scroll_to / on_scroll）；唯一子控件是 _text。
        # auto_scroll 永遠 False — 執行期切換會重建 Flutter ScrollController
        # 弄壞滾輪；build_controls_on_demand=False 是 scroll_to 生效的前提。
        self._list = ft.ListView(
            controls=[self._text],
            expand=True,
            auto_scroll=False,
            build_controls_on_demand=False,
            on_scroll=self._on_list_scroll,
            scroll_interval=50,
        )
        # 膠囊用 Row 沿底部定位（left/right/bottom）而非全區 Container：
        # Container 永遠支援 on_hover，Flutter 會包 MouseRegion 吸收滾輪與
        # 點擊；Row 不繪製也不命中空白區，事件能穿透到 ListView。
        self._pill = ft.Row(
            [
                ft.Container(
                    content=ft.Text("↓ 跳到最新", size=11, color=ft.Colors.WHITE),
                    bgcolor=ft.Colors.with_opacity(0.75, ft.Colors.BLUE_GREY_700),
                    border_radius=20,
                    padding=ft.Padding.symmetric(horizontal=16, vertical=7),
                    on_click=self._on_pill_click,
                    ink=True,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            left=0,
            right=0,
            bottom=15,
            visible=False,
        )
        # GestureDetector 只掛 on_scroll（Listener.onPointerSignal），
        # 不消費事件 — ListView 滾動與文字框選不受影響。
        self._gesture = ft.GestureDetector(
            content=ft.Container(
                content=self._list,
                expand=True,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=4,
                padding=4,
            ),
            on_scroll=self._on_wheel,
        )
        self.control = ft.Stack(
            controls=[self._gesture, self._pill],
            expand=True,
            fit=ft.StackFit.EXPAND,
        )

    # ---------------------------------------------------------------- 狀態

    @property
    def following(self) -> bool:
        return self._following

    @property
    def pill_visible(self) -> bool:
        return bool(self._pill.visible)

    def _set_following(self, following: bool) -> None:
        if self._following == following:
            return
        self._following = following
        self._pill.visible = not following
        # 事件處理器都在 event loop 上，直接 update 膠囊；detached 時靜默。
        with contextlib.suppress(Exception):
            self._pill.update()

    # ---------------------------------------------------------------- 事件

    def _on_wheel(self, e) -> None:
        delta = getattr(e, "scroll_delta", None)
        if delta is not None and (delta.y or 0) < 0:
            self._set_following(False)

    def _on_list_scroll(self, e) -> None:
        if e.event_type != ft.ScrollType.END:
            return
        if e.extent_after <= _BOTTOM_EPS:
            self._set_following(True)
        elif not self._scroll_pending:
            self._set_following(False)

    def _on_pill_click(self, e) -> None:
        self._set_following(True)
        self._schedule_scroll_to_bottom()

    # ---------------------------------------------------------------- append

    def append_log(self, html_line: str) -> None:
        spans = html_to_spans(html_line)
        if not spans:
            return
        spans.append(ft.TextSpan(text="\n"))
        self._text.spans.extend(spans)
        self._line_span_counts.append(len(spans))
        while len(self._line_span_counts) > self._max_lines:
            n = self._line_span_counts.pop(0)
            del self._text.spans[:n]
        # detached（如切到其他分頁）時不排程 scroll_to — 對已不渲染的
        # widget 發 _invoke_method 會在 event loop 堆積 awaited tasks。
        if self._following and self._is_attached():
            self._schedule_scroll_to_bottom()

    def _is_attached(self) -> bool:
        # Flet 0.84 的 Control.page 在 detached 時 raise RuntimeError。
        try:
            return self._list.page is not None
        except RuntimeError:
            return False

    # ---------------------------------------------------------------- 捲動

    def _schedule_scroll_to_bottom(self) -> None:
        """同一個 event-loop tick 內的多行 log 合併成一次 scroll_to。"""
        if self._scroll_pending:
            return
        self._scroll_pending = True
        try:
            asyncio.create_task(self._do_scroll_to_bottom())
        except RuntimeError:
            # 不在 event loop 上（理論上不會發生）— 安靜放棄。
            self._scroll_pending = False

    async def _do_scroll_to_bottom(self) -> None:
        try:
            await self._list.scroll_to(offset=-1, duration=0)
        except Exception:
            pass
        finally:
            self._scroll_pending = False
