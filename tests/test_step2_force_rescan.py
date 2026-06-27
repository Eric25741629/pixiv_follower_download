"""Step 2 force_rescan: a one-shot 'ignore the 30-day skip and re-scan all
artists' mode, used to backfill user_id for artists already scanned (now inside
the 30-day skip window). Also enables the full-artist user_id backfill even when
author_order is off."""
import datetime
import os
import sys
import threading
from queue import Queue

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.core.metadata_db import MetadataDB
from app.core.settings_store import DEFAULTS
from app.core.thread_pid_scan import get_pixiv_author_imgID_Thread


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


class _FakeQ:
    def __init__(self):
        self.events = []

    def put(self, ev):
        self.events.append(ev)


def _filter_thread(force):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.Author_list = ["1", "2", "3"]
    t._q = _FakeQ()
    t.force_rescan = force
    return t


def test_default_force_full_rescan_false():
    assert DEFAULTS["download"]["force_full_rescan"] is False


def test_filter_work_list_force_rescan_processes_all_despite_recent():
    t = _filter_thread(True)
    recent = datetime.datetime.now().isoformat()
    progress = {"1": recent, "2": recent, "3": recent}  # all scanned just now
    assert t._filter_work_list(progress) == ["1", "2", "3"]


def test_filter_work_list_without_force_skips_recent():
    t = _filter_thread(False)
    recent = datetime.datetime.now().isoformat()
    progress = {"1": recent, "2": recent, "3": recent}
    assert t._filter_work_list(progress) == []


def test_backfill_runs_when_force_rescan_even_if_author_order_off(tmp_path):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.author_order = False
    t.force_rescan = True
    t._metadata_db = MetadataDB(str(tmp_path), event_log=None)
    t._step2_db_write_lock = threading.Lock()
    t._q = _FakeQ()
    t._metadata_db.upsert_artworks(["10"])
    t._step2_backfill_author_user_ids(["10"], "A")
    assert t._metadata_db.user_id_map_for_pids(["10"])["10"] == "A"


def test_build_step2_reads_and_consumes_force_full_rescan(monkeypatch, tmp_path):
    import app.gui.run_actions as ra

    state = {"download": {"force_full_rescan": True, "author_order": False,
                          "path": str(tmp_path)}}

    class _FakeStore:
        def migrate_from_legacy(self):
            pass

        def get_section(self, name):
            if name == "download":
                return dict(state["download"])
            return {"auth": {"userid": "1", "cookies": "c1"}}.get(name, {})

        def update_fields(self, section, fields):
            state[section].update(fields)

    monkeypatch.setattr(ra, "_store", lambda: _FakeStore())
    monkeypatch.setattr(ra, "_data_path", lambda: str(tmp_path))
    monkeypatch.setattr(ra.RunController, "_validate_cookies_for_step",
                        lambda self, a, ag, n: ["c1"])
    monkeypatch.setattr(ra.RunController, "_build_scheduler",
                        lambda self, *a, **k: None)
    monkeypatch.setattr(ra, "_load_author_list", lambda: ["123"])

    rc = ra.RunController(main_view=object(), event_q=Queue())
    t = rc._build_thread(2)
    assert t is not None
    assert t.force_rescan is True
    # one-shot: the GUI flag is consumed (reset to False) after building.
    assert state["download"]["force_full_rescan"] is False
