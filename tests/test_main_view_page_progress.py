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


def test_page_progress_starts_hidden(view):
    assert view._page_progress_row.visible is False
    assert view._page_progress_text.value == ""


def test_update_page_progress_accumulates_delta_and_labels_pid(view):
    view.update_progress(1, 10)

    view.update_page_progress(1, 5, "123456")
    view.update_page_progress(1, 5, "123456")

    assert view._page_progress_row.visible is True
    assert view._page_progress_bar.value == pytest.approx(0.4)
    assert "PID 123456" in view._page_progress_text.value
    assert "分頁 2/5" in view._page_progress_text.value
    assert view._progress_value == 1
    assert view._progress_total == 10


def test_update_page_progress_resets_when_pid_changes(view):
    view.update_page_progress(2, 5, "111")

    view.update_page_progress(1, 3, "222")

    assert view._page_progress_bar.value == pytest.approx(1 / 3)
    assert "PID 222" in view._page_progress_text.value
    assert "分頁 1/3" in view._page_progress_text.value


def test_update_page_progress_hidden_for_single_page_artwork(view):
    """單張作品（total == 1）不顯示第二條進度條 — 1/1 沒有資訊量。"""
    view.update_page_progress(1, 1, "999")

    assert view._page_progress_row.visible is False
    assert view._page_progress_text.value == ""
    assert view._page_progress_bar.value == 0

    # A following multi-page artwork must still reveal the row.
    view.update_page_progress(1, 4, "1000")
    assert view._page_progress_row.visible is True
    assert "分頁 1/4" in view._page_progress_text.value


def test_update_page_progress_hides_for_non_positive_total_and_clear(view):
    view.update_page_progress(1, 5, "123")

    view.update_page_progress(0, 0, "123")

    assert view._page_progress_row.visible is False
    assert view._page_progress_text.value == ""
    assert view._page_progress_bar.value == 0

    view.update_page_progress(1, 5, "123")
    view.clear_page_progress()

    assert view._page_progress_row.visible is False
    assert view._page_progress_text.value == ""
    assert view._page_progress_bar.value == 0


def test_downloading_pid_shown_even_when_bar_hidden(view):
    """單頁作品隱藏分頁條，但 meta 列的「正在下載：PID」必須照常顯示。"""
    view.update_page_progress(1, 1, "999")

    assert view._page_progress_row.visible is False
    assert view._downloading_text.value == "正在下載：PID 999"


def test_downloading_pid_updates_and_clears_on_terminal_clear(view):
    view.update_page_progress(1, 4, "123")
    assert view._downloading_text.value == "正在下載：PID 123"

    view.update_page_progress(1, 3, "456")
    assert view._downloading_text.value == "正在下載：PID 456"

    # terminal clear（finished / next=-1 / combined 每 PID 收尾）才清掉
    view.clear_page_progress()
    assert view._downloading_text.value == ""


def test_downloading_text_sits_before_eta_on_meta_row(view):
    controls = view._meta_row.controls
    assert controls.index(view._downloading_text) < controls.index(view._eta_text)
    assert controls.index(view._eta_text) < controls.index(view._countdown_text)
