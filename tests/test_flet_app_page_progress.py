from types import SimpleNamespace


def test_page_progress_event_handler_accepts_dict_payload(monkeypatch):
    from app.core import app_logging

    monkeypatch.setattr(app_logging, "_INITIALIZED", True)
    monkeypatch.setattr(app_logging, "_LOG_PATH", "")

    from app.gui import flet_app

    view = SimpleNamespace(calls=[], clear_page_progress=lambda: None)
    view.update_page_progress = lambda delta, total, pid="": view.calls.append(
        (delta, total, pid)
    )

    flet_app._handle_page_progress_event(
        view,
        {"delta": 2, "total": 5, "pid": "123456"},
    )

    assert view.calls == [(2, 5, "123456")]


def test_page_progress_event_handler_accepts_tuple_payload(monkeypatch):
    from app.core import app_logging

    monkeypatch.setattr(app_logging, "_INITIALIZED", True)
    monkeypatch.setattr(app_logging, "_LOG_PATH", "")

    from app.gui import flet_app

    view = SimpleNamespace(calls=[], clear_page_progress=lambda: None)
    view.update_page_progress = lambda delta, total, pid="": view.calls.append(
        (delta, total, pid)
    )

    flet_app._handle_page_progress_event(view, (1, 3, "654321"))

    assert view.calls == [(1, 3, "654321")]


def test_page_progress_event_handler_clears_empty_payload(monkeypatch):
    from app.core import app_logging

    monkeypatch.setattr(app_logging, "_INITIALIZED", True)
    monkeypatch.setattr(app_logging, "_LOG_PATH", "")

    from app.gui import flet_app

    view = SimpleNamespace(cleared=0, update_page_progress=lambda *args: None)

    def clear():
        view.cleared += 1

    view.clear_page_progress = clear

    flet_app._handle_page_progress_event(view, None)

    assert view.cleared == 1
