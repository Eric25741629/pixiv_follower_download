from types import SimpleNamespace


def test_activate_view_keeps_all_views_mounted_and_only_toggles_visibility(
    monkeypatch,
):
    from app.core import app_logging

    monkeypatch.setattr(app_logging, "_INITIALIZED", True)
    monkeypatch.setattr(app_logging, "_LOG_PATH", "")

    from app.gui import flet_app

    views = [SimpleNamespace(visible=True) for _ in range(4)]

    flet_app._activate_view(views, 2)

    assert [v.visible for v in views] == [False, False, True, False]


def test_activate_view_falls_back_to_first_view(monkeypatch):
    from app.core import app_logging

    monkeypatch.setattr(app_logging, "_INITIALIZED", True)
    monkeypatch.setattr(app_logging, "_LOG_PATH", "")

    from app.gui import flet_app

    views = [SimpleNamespace(visible=True) for _ in range(3)]

    flet_app._activate_view(views, 99)

    assert [v.visible for v in views] == [True, False, False]
