"""LogPanel 跟隨狀態機 + 單一 selectable Text 跨行複製結構的單元測試。

對應 spec: docs/superpowers/specs/2026-06-10-log-follow-rearchitecture-design.md
狀態機規則:
  - 滾輪向上 (GestureDetector scroll_delta.y < 0) → following=False
  - ListView ScrollType.END 且 extent_after <= 門檻 → following=True
  - ListView ScrollType.END 且離底且無程式捲動排程 → following=False
  - 膠囊點擊 → following=True + 排程捲動到底
  - 膠囊可見性恆等於 not following
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import flet as ft

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gui.log_panel import LogPanel  # noqa: E402


def make_panel(**kw) -> LogPanel:
    return LogPanel(**kw)


def wheel(dy: float) -> SimpleNamespace:
    return SimpleNamespace(scroll_delta=SimpleNamespace(x=0.0, y=dy))


def list_end(extent_after: float) -> SimpleNamespace:
    return SimpleNamespace(event_type=ft.ScrollType.END, extent_after=extent_after)


def list_update(extent_after: float) -> SimpleNamespace:
    return SimpleNamespace(event_type=ft.ScrollType.UPDATE, extent_after=extent_after)


# ---------------------------------------------------------------- 狀態機

def test_initial_state_following_pill_hidden():
    p = make_panel()
    assert p.following is True
    assert p.pill_visible is False


def test_wheel_up_disables_following():
    p = make_panel()
    p._on_wheel(wheel(-40.0))
    assert p.following is False
    assert p.pill_visible is True


def test_wheel_down_alone_does_not_change_state():
    p = make_panel()
    p._on_wheel(wheel(-40.0))
    p._on_wheel(wheel(+40.0))  # 滾輪向下不直接恢復 — 靠 END 貼底事件
    assert p.following is False


def test_end_at_bottom_re_enables_following():
    p = make_panel()
    p._on_wheel(wheel(-40.0))
    p._on_list_scroll(list_end(extent_after=0.0))
    assert p.following is True
    assert p.pill_visible is False


def test_end_near_bottom_within_eps_re_enables():
    p = make_panel()
    p._on_wheel(wheel(-40.0))
    p._on_list_scroll(list_end(extent_after=7.5))
    assert p.following is True


def test_end_away_from_bottom_disables_following():
    # 涵蓋拖捲軸條上滾（不經過滾輪路徑）
    p = make_panel()
    assert p.following is True
    p._on_list_scroll(list_end(extent_after=300.0))
    assert p.following is False
    assert p.pill_visible is True


def test_end_away_from_bottom_ignored_while_scroll_pending():
    # 程式捲動排程中（log 暴增），離底 END 不得誤關跟隨
    p = make_panel()
    p._scroll_pending = True
    p._on_list_scroll(list_end(extent_after=300.0))
    assert p.following is True


def test_update_events_are_ignored():
    # 只有 END 定案事件參與狀態機 — UPDATE 節流漏事件不再影響行為
    p = make_panel()
    p._on_list_scroll(list_update(extent_after=500.0))
    assert p.following is True
    p._on_wheel(wheel(-40.0))
    p._on_list_scroll(list_update(extent_after=0.0))
    assert p.following is False


def test_pill_click_re_enables_and_schedules_scroll():
    p = make_panel()
    p._on_wheel(wheel(-40.0))
    scheduled = []
    p._schedule_scroll_to_bottom = lambda: scheduled.append(True)
    p._on_pill_click(None)
    assert p.following is True
    assert p.pill_visible is False
    assert scheduled == [True]


def test_pill_visibility_always_mirrors_not_following():
    p = make_panel()
    for ev in [wheel(-1.0), wheel(-1.0)]:
        p._on_wheel(ev)
        assert p.pill_visible == (not p.following)
    p._on_list_scroll(list_end(extent_after=0.0))
    assert p.pill_visible == (not p.following)


# ---------------------------------------------------------------- append / 裁切

def test_append_log_appends_spans_with_newline():
    p = make_panel()
    p.append_log("<p><font color='red'>錯誤</font></p>")
    spans = p._text.spans
    assert spans[-1].text == "\n"
    assert any(s.text == "錯誤" for s in spans)


def test_append_log_empty_html_is_noop():
    p = make_panel()
    p.append_log("")
    assert p._text.spans == []


def test_append_log_trims_oldest_line_span_block():
    p = make_panel(max_lines=2)
    p.append_log("<p>line-A</p>")
    p.append_log("<p><font color='blue'>B1</font>B2</p>")  # 多 span 行
    p.append_log("<p>line-C</p>")
    text = "".join(s.text or "" for s in p._text.spans)
    assert "line-A" not in text
    assert "B1" in text and "B2" in text and "line-C" in text
    # span 計數簿記與實際 spans 一致
    assert sum(p._line_span_counts) == len(p._text.spans)


def test_append_log_does_not_schedule_scroll_when_not_following():
    p = make_panel()
    p._on_wheel(wheel(-40.0))
    called = []
    p._schedule_scroll_to_bottom = lambda: called.append(True)
    p.append_log("<p>x</p>")
    assert called == []


def test_append_log_does_not_schedule_scroll_when_detached():
    # _list.page is None（測試環境必然 detached）→ 不排程
    p = make_panel()
    called = []
    p._schedule_scroll_to_bottom = lambda: called.append(True)
    p.append_log("<p>x</p>")
    assert called == []


# ---------------------------------------------------------------- relayout

def test_notify_relayout_schedules_scroll_when_following_and_attached():
    # 按下停止（show_dialog + page.update）等整頁重排會把 log 捲回頂部 —
    # 跟隨中必須排程跳回底部。
    p = make_panel()
    called = []
    p._schedule_scroll_to_bottom = lambda: called.append(True)
    p._is_attached = lambda: True
    p.notify_relayout()
    assert called == [True]


def test_notify_relayout_noop_when_not_following():
    p = make_panel()
    p._on_wheel(wheel(-40.0))
    called = []
    p._schedule_scroll_to_bottom = lambda: called.append(True)
    p._is_attached = lambda: True
    p.notify_relayout()
    assert called == []


def test_notify_relayout_noop_when_detached():
    p = make_panel()  # 測試環境必然 detached
    called = []
    p._schedule_scroll_to_bottom = lambda: called.append(True)
    p.notify_relayout()
    assert called == []


# ---------------------------------------------------------------- 控件結構

def test_text_is_single_selectable_control():
    p = make_panel()
    assert p._text.selectable is True
    assert p._list.controls == [p._text]


def test_listview_settings_for_scroll_to():
    p = make_panel()
    assert p._list.auto_scroll is False
    assert p._list.build_controls_on_demand is False


def test_control_is_stack_with_gesture_and_pill():
    p = make_panel()
    assert isinstance(p.control, ft.Stack)
    assert p._gesture in p.control.controls
    assert p._pill in p.control.controls
