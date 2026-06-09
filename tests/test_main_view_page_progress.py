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
