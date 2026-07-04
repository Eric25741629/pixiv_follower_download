"""MainView per-worker lane panel (parallel combined mode)."""
import os
import sys
from queue import Queue

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class _FakePage:
    """MainView only needs theme lookups for these unit tests."""


@pytest.fixture
def view():
    from app.gui.views.main_view import MainView

    return MainView(_FakePage(), Queue())


def test_lane_panel_starts_hidden_empty(view):
    assert view._lane_panel.visible is False
    assert view._lane_panel.controls == []
    assert view._lane_rows == {}


def test_init_lanes_builds_k_rows_and_shows(view):
    view.init_lanes(2)
    assert view._lane_panel.visible is True
    assert len(view._lane_panel.controls) == 2
    assert set(view._lane_rows) == {0, 1}


def test_lane_row_starts_hidden_and_reveals_on_first_update(view):
    """The load-bearing Flet 0.84 reflow fix: rows are created visible=False and
    only flip to True on first real data, forcing a relayout with content."""
    view.init_lanes(2)
    assert view._lane_rows[0]["row"].visible is False
    assert view._lane_rows[1]["row"].visible is False
    view.update_lane(0, alias="A", pid="1", state="下載", page=1, total=3)
    assert view._lane_rows[0]["row"].visible is True   # revealed
    assert view._lane_rows[1]["row"].visible is False  # untouched lane stays hidden


def test_update_lane_renders_cookie_pid_and_bar(view):
    view.init_lanes(2)
    view.update_lane(0, alias="cookieA", pid="143429333", state="下載", page=7, total=15)
    lane = view._lane_rows[0]
    assert lane["alias"].value == "cookieA"
    assert lane["bar"].value == pytest.approx(7 / 15)
    assert "PID 143429333" in lane["status"].value
    assert "分頁 7/15" in lane["status"].value


def test_update_lane_waiting_state_shows_cooldown_hint(view):
    view.init_lanes(1)
    view.update_lane(0, pid="1", state="等待", page=0, total=0)
    lane = view._lane_rows[0]
    assert lane["bar"].value == 0
    assert "等待" in lane["status"].value


def test_lane_countdown_is_per_lane_not_shared(view):
    """Each waiting lane counts down its OWN worker's wait (the per-slot
    ``wait`` field); a global countdown tick never syncs across lanes."""
    view.init_lanes(3)
    view.update_lane(0, pid="1", state="等待", page=0, total=0, wait=23)
    view.update_lane(1, pid="2", state="等待", page=0, total=0, wait=7)
    view.update_lane(2, alias="B", pid="3", state="下載", page=1, total=3)
    assert "23" in view._lane_rows[0]["status"].value
    assert "7" in view._lane_rows[1]["status"].value
    assert "23" not in view._lane_rows[1]["status"].value  # not mirrored
    assert "23" not in view._lane_rows[2]["status"].value  # busy lane untouched
    view.update_lane(0, wait=0)  # this worker's pickup fired -> suffix clears
    assert "23" not in view._lane_rows[0]["status"].value
    assert "等待" in view._lane_rows[0]["status"].value
    assert "7" in view._lane_rows[1]["status"].value       # other lane unaffected


def test_waiting_lane_shows_its_assigned_pid(view):
    """A waiting lane displays WHICH PID this slot is about to process."""
    view.init_lanes(2)
    view.update_lane(0, pid="125400331", state="等待", page=0, total=0, wait=5)
    view.update_lane(1, pid="", state="等待", page=0, total=0, wait=0)
    assert "125400331" in view._lane_rows[0]["status"].value
    assert "5" in view._lane_rows[0]["status"].value
    assert "PID" not in view._lane_rows[1]["status"].value  # idle slot: plain text


def test_global_countdown_does_not_touch_lanes(view):
    view.init_lanes(1)
    view.update_lane(0, pid="1", state="等待", page=0, total=0, wait=0)
    before = view._lane_rows[0]["status"].value
    view.update_countdown(42)
    assert view._lane_rows[0]["status"].value == before
    assert "42" not in view._lane_rows[0]["status"].value


def test_countdown_never_reveals_fresh_lane(view):
    """A lane with no data yet (state='') must stay hidden — _render_lane's
    reveal toggle is load-bearing for Flet 0.84 reflow."""
    view.init_lanes(1)
    view.update_countdown(10)
    assert view._lane_rows[0]["row"].visible is False


def test_update_lane_merges_partial_fields(view):
    view.init_lanes(1)
    view.update_lane(0, alias="A", pid="9", state="下載", page=1, total=4)
    view.update_lane(0, page=3)  # only page changes; alias/total persist
    lane = view._lane_rows[0]
    assert lane["alias"].value == "A"
    assert lane["bar"].value == pytest.approx(3 / 4)
    assert "分頁 3/4" in lane["status"].value


def test_update_lane_unknown_slot_is_noop(view):
    view.init_lanes(1)
    view.update_lane(5, alias="X", pid="1", state="下載", page=1, total=2)  # no crash
    assert 5 not in view._lane_rows


def test_clear_lanes_hides_and_empties(view):
    view.init_lanes(2)
    view.clear_lanes()
    assert view._lane_panel.visible is False
    assert view._lane_panel.controls == []
    assert view._lane_rows == {}
