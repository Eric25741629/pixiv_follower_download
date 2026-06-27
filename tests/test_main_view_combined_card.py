"""When 邊查邊下 (combined mode) is on, the main view must merge the step 3
and step 4 cards into a single 「步驟 3+4 邊查邊下」 card (step 4 hidden), and
revert to the 4-card layout when off.
"""
import os
import sys
from queue import Queue

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest

import app.i18n as i18n


def setup_function(_fn):
    # Step-card labels resolve via i18n.t(); pin zh-TW so the assertions below
    # (which compare against the zh helper output) are stable regardless of
    # test ordering.
    i18n.set_locale(i18n.BASE_LOCALE)


class _FakePage:
    """MainView.__init__ only reads theme via getattr; a bare object suffices."""


@pytest.fixture
def view():
    from app.gui.views.main_view import MainView

    return MainView(_FakePage(), Queue())


def test_apply_combined_mode_merges_cards(view):
    from app.gui.views.main_view import merged_step3_label

    view.apply_combined_mode(True)
    assert view._step_card_texts[2].value == merged_step3_label()
    assert view._step_cards[3].visible is False
    assert view._combined_mode is True


def test_apply_combined_mode_restores_default(view):
    from app.gui.views.main_view import step_labels

    view.apply_combined_mode(True)
    view.apply_combined_mode(False)
    assert view._step_card_texts[2].value == step_labels()[2]
    assert view._step_cards[3].visible is True
    assert view._combined_mode is False


def test_refresh_combined_mode_reads_setting(view, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.core.settings_store import SettingsStore
    from app.gui.views.main_view import merged_step3_label

    base = os.getenv("APPDATA") + r"/pixiv_download/"
    SettingsStore(base).update_fields("download", {"combined_mode": True})

    view.refresh_combined_mode()
    assert view._step_card_texts[2].value == merged_step3_label()
    assert view._step_cards[3].visible is False


def test_refresh_combined_mode_off_keeps_default(view, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.core.settings_store import SettingsStore
    from app.gui.views.main_view import step_labels

    base = os.getenv("APPDATA") + r"/pixiv_download/"
    SettingsStore(base).update_fields("download", {"combined_mode": False})

    view.refresh_combined_mode()
    assert view._step_card_texts[2].value == step_labels()[2]
    assert view._step_cards[3].visible is not False
