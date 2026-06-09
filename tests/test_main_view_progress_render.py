"""Regression tests for the 整體進度 / 本作分頁 progress block.

Covers the bug where the overall progress text + ETA never rendered during a
run (only the parent Row's children were updated, never the Row itself, so a
Row holding an expand=True ProgressBar never reflowed its text) plus the
alignment / thickness redesign.
"""
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


def _spy_updates(*controls):
    """Replace each control's update() with a recorder and return the log."""
    seen = []
    for c in controls:
        c.update = (lambda ctrl=c: seen.append(ctrl))  # noqa: E731
    return seen


# ── the actual bug: the overall row must be repainted, not just its children ──

def test_update_progress_repaints_the_parent_row(view):
    seen = _spy_updates(view._progress_row)

    view.update_progress(0, 100)   # phase reset
    view.update_progress(5, 100)

    # The Row itself (not only _progress_text) must be told to update so the
    # text reflows next to the expand=True ProgressBar.
    assert view._progress_row in seen
    assert view._progress_text.value == "5 / 100"
    assert view._progress_bar.value == pytest.approx(0.05)


def test_update_progress_shows_total_immediately_on_reset(view):
    view.update_progress(0, 320)
    # Even at 0 done, the denominator (總量) must be visible.
    assert "320" in view._progress_text.value


def test_update_countdown_repaints_meta_row(view):
    seen = _spy_updates(view._meta_row)
    view.update_countdown(8)
    assert view._meta_row in seen
    assert "8" in view._countdown_text.value
    view.update_countdown(0)
    assert view._countdown_text.value == ""


def test_eta_renders_with_progress(view):
    seen = _spy_updates(view._meta_row)
    view.update_progress(0, 10)
    view.update_progress(1, 10)
    # ETA lives on the meta row and must be repainted with progress.
    assert view._meta_row in seen


# ── alignment: both bars share identical leading / trailing geometry ──────────

def test_overall_and_page_bars_are_aligned(view):
    top = view._progress_row.controls
    page = view._page_progress_row.controls

    # same column count: [lead, bar, trailing]
    assert len(top) == 3 and len(page) == 3
    # identical lead width, trailing width and row spacing => bars line up
    assert top[0].width == page[0].width
    assert top[2].width == page[2].width
    assert view._progress_row.spacing == view._page_progress_row.spacing
    # the bar is the expanding middle column in both
    assert view._progress_bar.expand is True
    assert view._page_progress_bar.expand is True


# ── thickness: no more hairline ───────────────────────────────────────────────

def test_bars_are_thicker_than_default(view):
    assert (view._progress_bar.bar_height or 0) >= 8
    assert (view._page_progress_bar.bar_height or 0) >= 8


# ── build wires the new rows into the column ──────────────────────────────────

def test_build_includes_progress_and_meta_rows(view):
    col = view.build()
    assert view._progress_row in col.controls
    assert view._page_progress_row in col.controls
    assert view._meta_row in col.controls


# ── the freeze fix: BOTH rows are visible-gated & built identically ───────────

def test_both_progress_rows_built_identically(view):
    """The two bars must be constructed the same way (the user's complaint:
    'two progress bars built differently is a mistake'). Same column shape,
    same geometry, and crucially BOTH start hidden so they share the
    reveal-on-first-update lifecycle that makes 本作分頁 render live."""
    top = view._progress_row
    page = view._page_progress_row
    assert top.visible is False and page.visible is False
    assert len(top.controls) == len(page.controls) == 3
    assert top.controls[0].width == page.controls[0].width
    assert top.controls[2].width == page.controls[2].width
    assert top.spacing == page.spacing
    assert view._progress_bar.expand is view._page_progress_bar.expand is True
    assert view._progress_bar.bar_height == view._page_progress_bar.bar_height


def test_update_progress_reveals_overall_row(view):
    """Regression for the 整體進度 freeze: an always-visible row laid out with
    empty children never reflowed later .value patches. The fix reveals the row
    on first real data (visible False -> True) exactly like 本作分頁."""
    assert view._progress_row.visible is False
    view.update_progress(0, 320)          # phase reset, total known
    assert view._progress_row.visible is True
    assert "320" in view._progress_text.value


def test_overall_row_hidden_again_when_total_zero(view):
    view.update_progress(0, 320)
    assert view._progress_row.visible is True
    view.update_progress(0, 0)            # idle / no work
    assert view._progress_row.visible is False
    assert view._progress_text.value == ""


def test_paint_progress_reveals_on_post_gc_restore(view):
    """build() paints restored progress; with total>0 the row must be revealed
    so a reattached worker's bar shows immediately (not a hidden 0/0)."""
    view._progress_value = 116
    view._progress_total = 320
    view.build()
    assert view._progress_row.visible is True
    assert view._progress_text.value == "116 / 320"
