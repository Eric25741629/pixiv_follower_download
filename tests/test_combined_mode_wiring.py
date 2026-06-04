import os, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from queue import Queue
from app.core.thread_combined import combined_thread


def _make():
    q = Queue()
    path = tempfile.mkdtemp()
    t = combined_thread(
        q=q,
        Author_list=[],
        Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}],
        exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[],
        base_path=path,
        single_thread_mode=True,
        download_path=path,
        download_time=__import__("datetime").datetime(1970, 1, 1),
    )
    return t


def test_combined_shares_events_and_db_between_engines():
    t = _make()
    assert t.fetcher._pause_event is t._pause_event
    assert t.fetcher._stop_event is t._stop_event
    assert t.downloader._pause_event is t._pause_event
    assert t.downloader._stop_event is t._stop_event
    assert t.downloader._metadata_db is t.fetcher._metadata_db


def test_build_thread_3_returns_combined_when_enabled(monkeypatch, tmp_path):
    import app.gui.run_actions as ra

    captured = {}

    class _FakeStore:
        def migrate_from_legacy(self): pass
        def get_section(self, name):
            return {
                "auth": {"userid": "1", "cookies": "c1"},
                "download": {"path": str(tmp_path), "combined_mode": True,
                             "ban_tag": [], "must_tag": [], "like_num": 0},
                "filter": {}, "performance": {}, "directory": {}, "jxl": {},
            }[name]

    monkeypatch.setattr(ra, "_store", lambda: _FakeStore())
    monkeypatch.setattr(ra, "_data_path", lambda: str(tmp_path))
    monkeypatch.setattr(ra.RunController, "_validate_cookies_for_step",
                        lambda self, a, ag, n: ["c1"])
    monkeypatch.setattr(ra.RunController, "_sync_exist_pid_from_download_folder",
                        lambda *a, **k: None)
    monkeypatch.setattr(ra.RunController, "_build_scheduler",
                        lambda self, *a, **k: None)
    monkeypatch.setattr(ra, "_load_author_list", lambda: [])

    from app.core.thread_combined import combined_thread
    rc = ra.RunController(main_view=object(), event_q=__import__("queue").Queue())
    t = rc._build_thread(3)
    captured["t"] = t
    assert isinstance(t, combined_thread)
