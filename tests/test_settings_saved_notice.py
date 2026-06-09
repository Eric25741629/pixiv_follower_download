from pathlib import Path
import queue
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.settings_store import SettingsStore
from app.core.worker_event import WorkerEvent
from app.gui.run_actions import RunController


class _Thread:
    def __init__(self, alive=True):
        self._alive = alive

    def is_alive(self):
        return self._alive


class _MainView:
    def __init__(self, active_thread=None):
        self._active_thread = active_thread


def _write_settings(base, **sections):
    store = SettingsStore(str(base))
    data = store.load()
    for section, values in sections.items():
        data[section].update(values)
    store.save(data)


def _controller(tmp_path, active_thread):
    event_q = queue.Queue()
    rc = RunController(_MainView(active_thread), event_q)
    rc._live = __import__("app.core.live_settings", fromlist=["LiveSettings"]).LiveSettings(str(tmp_path))
    rc._active_snapshot = rc._live.sections()
    return rc, event_q


def test_settings_saved_notice_emits_live_and_next_run_messages(tmp_path):
    _write_settings(tmp_path)
    rc, event_q = _controller(tmp_path, _Thread(alive=True))

    _write_settings(
        tmp_path,
        download={"like_num": 99},
        performance={"single_thread_mode": True},
    )

    rc.notify_settings_saved()

    events = [event_q.get_nowait(), event_q.get_nowait()]
    assert all(isinstance(e, WorkerEvent) for e in events)
    html = "\n".join(e.data for e in events)
    assert "已即時套用" in html
    assert "最低讚數" in html
    assert "將於下次執行套用" in html
    assert "單執行緒" in html


def test_settings_saved_notice_skips_when_no_active_thread(tmp_path):
    _write_settings(tmp_path)
    rc, event_q = _controller(tmp_path, None)

    _write_settings(tmp_path, download={"like_num": 99})
    rc.notify_settings_saved()

    assert event_q.empty()


def test_settings_view_save_and_autosave_call_on_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.gui.views.settings_view import SettingsView

    class _Services:
        def extend(self, _items):
            pass

    class _Page:
        services = _Services()

    calls = []
    view = SettingsView(_Page(), on_saved=lambda: calls.append("saved"))

    view.save()
    view._sw_nogif.value = True
    view._sw_nogif.on_change(None)

    assert calls == ["saved", "saved"]
