"""Dual progress-bar subsystem for ``MainView`` (file-size refactor).

The 整體進度 / 本作分頁 stacked bars, their single shared render path, the ETA
formatter and the countdown line — mixed into ``MainView`` via
``_MainProgressMixin``. Every method uses only ``self.`` for cross-method calls
(resolved through inheritance) plus the module-level names imported below, so
behavior is byte-for-byte identical to the originals. Controls these methods
read/write (``self._progress_row``, ``self._meta_row``, ``self._downloading_text``,
``self._set_downloading_pid`` / ``self._set_downloading_status`` /
``self._safe_update`` …) are created in ``MainView.__init__`` / live on the
concrete class.
"""
from __future__ import annotations

import time

import flet as ft

from app import i18n
from app.gui.glass import GlassProgressBar, current_theme

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


class _MainProgressMixin:
    """Dual progress bars + ETA + countdown, mixed into ``MainView``."""

    def _make_progress_row(self, label: str, color: str):
        """Build one labeled progress row: [label] [expand bar] [count text].

        Returns ``(bar, text, row)``. Used for BOTH 整體進度 and 本作分頁 so the
        two bars are constructed identically (same geometry); ``color`` differs
        per row (accent vs info) so the two bars are visually distinct.
        The row starts hidden — it is revealed on its first real update by
        :meth:`_render_progress_row`.
        """
        theme = current_theme(self._page)
        # GlassProgressBar（圓角 track + 圓角 fill）取代 ft.ProgressBar —
        # M3 進度條的 fill/track 切邊是直角，與全圓角的玻璃語彙混搭突兀。
        bar = GlassProgressBar(
            color=color,
            bar_height=_PROG_BAR_HEIGHT,
            radius=_PROG_BAR_RADIUS,
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
                bar.track,
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
        # 「正在下載：PID」獨立於分頁條 — 單頁作品也要顯示目前下載對象。
        if pid_text:
            self._set_downloading_pid(pid_text)
        # 單張作品（page_count == 1）沒有「分頁」可言 — 顯示 1/1 只是噪音，
        # 只隱藏第二條進度條（meta 列的 PID／倒數照常顯示）。
        if t <= 1:
            self._hide_page_progress_bar()
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
        label = i18n.t("main.page.label", pid=pid_text) if pid_text else i18n.t("main.page.label_nopid")
        trailing = f"{label} {self._page_progress_value}/{self._page_progress_total}"
        self._render_progress_row(
            self._page_progress_row, self._page_progress_bar,
            self._page_progress_text, self._page_progress_value, t, trailing,
        )

    def _hide_page_progress_bar(self) -> None:
        """只收掉第二條進度條本體，保留 meta 列（正在下載／倒數）。"""
        self._page_progress_value = 0
        self._page_progress_total = 0
        self._page_progress_pid = ""
        self._render_progress_row(
            self._page_progress_row, self._page_progress_bar,
            self._page_progress_text, 0, 0, "",
        )

    def clear_page_progress(self) -> None:
        self._hide_page_progress_bar()
        self._set_downloading_pid("")

    # ── per-worker lanes (parallel combined mode) ────────────────────────────
    # One row per concurrent worker: [cookie alias] [page bar] [PID 分頁 m/n].
    # Driven by lanes_init / lane / lanes_clear events; the panel is hidden
    # unless a concurrent run is active (sequential runs keep the single 本作分頁
    # bar above).
    def _make_lane_row(self):
        theme = current_theme(self._page)
        bar = GlassProgressBar(
            color=theme.info, bar_height=_PROG_BAR_HEIGHT, radius=_PROG_BAR_RADIUS,
        )
        alias = ft.Text("", size=12, color=theme.text_secondary,
                        width=_PROG_LEAD_W, no_wrap=True)
        status = ft.Text("", size=13, color=theme.text_primary,
                         weight=ft.FontWeight.W_600, width=_PROG_TRAIL_W,
                         text_align=ft.TextAlign.LEFT)
        # Start hidden: the visible False->True toggle on first real data forces
        # Flet 0.84 to (re)lay out the row with content. A row first laid out with
        # empty children renders degenerate and later .value patches never reflow
        # it — the documented 整體進度/本作分頁 freeze (see _render_progress_row).
        row = ft.Row(
            controls=[alias, bar.track, status],
            spacing=_PROG_ROW_SPACING,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
        )
        return {"bar": bar, "alias": alias, "status": status, "row": row,
                "state": {"alias": "", "pid": "", "state": "", "page": 0,
                          "total": 0, "wait": 0}}

    def init_lanes(self, count) -> None:
        try:
            n = int(count)
        except (TypeError, ValueError):
            return
        self._lane_rows = {}
        rows = []
        for slot in range(max(0, n)):
            lane = self._make_lane_row()
            self._lane_rows[slot] = lane
            rows.append(lane["row"])
        self._lane_panel.controls = rows
        self._lane_panel.visible = n > 0
        # Cap the panel height past ~6 lanes so a large worker count scrolls
        # instead of squeezing the log / controls off-screen (~34px per row).
        self._lane_panel.height = (min(n, 6) * 34) if n > 6 else None
        self._safe_update(self._lane_panel)

    def update_lane(self, slot, **fields) -> None:
        try:
            s = int(slot)
        except (TypeError, ValueError):
            return
        lane = (getattr(self, "_lane_rows", None) or {}).get(s)
        if lane is None:
            return
        st = lane["state"]
        for k in ("alias", "pid", "state", "page", "total", "wait"):
            if k in fields and fields[k] is not None:
                st[k] = fields[k]
        self._render_lane(lane)

    def _render_lane(self, lane) -> None:
        st = lane["state"]
        alias = str(st.get("alias") or "")
        pid = str(st.get("pid") or "")
        state = str(st.get("state") or "")
        try:
            page = int(st.get("page") or 0)
            total = int(st.get("total") or 0)
        except (TypeError, ValueError):
            page, total = 0, 0
        lane["alias"].value = alias or "—"
        if state == "下載" and total > 0:
            lane["bar"].value = max(0.0, min(1.0, page / total))
            lane["status"].value = i18n.t("main.lane.downloading", pid=pid, page=page, total=total)
        elif state == "下載":
            lane["bar"].value = 0
            lane["status"].value = i18n.t("main.lane.downloading_started", pid=pid)
        elif state == "查詢":
            lane["bar"].value = 0
            lane["status"].value = i18n.t("main.lane.querying", pid=pid)
        else:  # 等待 / cooldown between PIDs
            lane["bar"].value = 0
            # Per-lane countdown: each worker streams its OWN remaining wait
            # (st["wait"]) — lanes never share/mirror one global countdown.
            try:
                r = int(st.get("wait") or 0)
            except (TypeError, ValueError):
                r = 0
            if pid:
                lane["status"].value = (
                    i18n.t("main.lane.waiting_pid_countdown", pid=pid, r=r) if r > 0
                    else i18n.t("main.lane.waiting_pid", pid=pid)
                )
            else:
                lane["status"].value = (
                    i18n.t("main.lane.waiting_countdown", r=r) if r > 0
                    else i18n.t("main.lane.waiting")
                )
        # Reveal on first real data — the load-bearing reflow toggle.
        lane["row"].visible = True
        self._safe_update(lane["row"])

    def clear_lanes(self) -> None:
        self._lane_rows = {}
        self._lane_panel.controls = []
        self._lane_panel.visible = False
        self._safe_update(self._lane_panel)

    def _format_eta(self, now: float) -> str:
        if self._progress_started_at is None:
            return ""
        if self._progress_value <= 0 or self._progress_total <= 0:
            return ""
        if self._progress_value >= self._progress_total:
            return i18n.t("main.eta.done")
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
            return i18n.t("main.eta.hms", h=h, m=m, s=s)
        m, s = divmod(eta_sec, 60)
        return i18n.t("main.eta.ms", m=m, s=s)

    def update_countdown(self, remaining: int) -> None:
        try:
            r = int(remaining)
        except (TypeError, ValueError):
            r = 0
        self._countdown_text.value = i18n.t("main.countdown", r=r) if r > 0 else ""
        # Update the meta Row, not just the Text, so it reflows reliably.
        self._safe_update(self._meta_row)
        # NOTE: deliberately NOT mirrored onto lanes — each lane's countdown is
        # its own worker's wait, streamed per-slot via the "lane" event (wait=N).
