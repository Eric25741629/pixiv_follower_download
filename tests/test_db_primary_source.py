"""Verify SQLite-as-primary-source behaviour after the JSON-removal migration.

Scenarios:
1. Step 3/4 startup prefers DB when meta_count > 0, ignores JSON
2. Step 3/4 startup falls back to JSON when DB is empty, imports into DB
3. _maybe_flush_exist_pid marks downloaded in DB immediately
4. _persist_url_meta (Step 4) syncs url_meta into DB (no JSON written)
5. _persist_step3_url_meta (Step 3) syncs url_meta into DB (no JSON written)
6. run_actions._backup_db triggers backup_file on the .db path
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from queue import Queue
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB, DB_FILENAME
from app.core.thread_download import download_thread
from app.core.thread_url_fetch import get_img_url_thread


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_step4(tmp_path):
    t = download_thread.__new__(download_thread)
    t.url_meta = {}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t.path = str(tmp_path)
    t._url_meta_lock = threading.RLock()
    t._metadata_db = MetadataDB(str(tmp_path))
    return t


def _make_step3(tmp_path):
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.url_meta = {}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t.path = str(tmp_path)
    t._q = Queue()
    t._metadata_db = MetadataDB(str(tmp_path))
    return t


# ── 1. Startup prefers DB ────────────────────────────────────────────────────

def test_step4_get_meta_reads_from_db(tmp_path):
    # _load_initial_url_meta returns {} — data is fetched on-demand via _get_meta
    t = _make_step4(tmp_path)
    t._metadata_db.upsert_meta("111", tags=["db_tag"], like_count=10)

    assert t._load_initial_url_meta() == {}  # no bulk load
    meta = t._get_meta("111")
    assert meta["tag"] == ["db_tag"]
    assert meta["like"] == 10


def test_step3_get_meta_reads_from_db(tmp_path):
    t = _make_step3(tmp_path)
    t._metadata_db.upsert_meta("333", tags=["db_tag"], like_count=5)

    assert t._load_initial_url_meta() == {}
    meta = t._get_meta("333")
    assert meta["tag"] == ["db_tag"]


# ── 2. _get_meta falls back to in-memory write-through on cache hit ───────────

def test_step4_get_meta_uses_in_memory_cache(tmp_path):
    t = _make_step4(tmp_path)
    t.url_meta["555"] = {"tag": ["cached"], "like": 3, "pagecount": 1, "img_url": "u"}

    meta = t._get_meta("555")
    assert meta["tag"] == ["cached"]


def test_step3_get_meta_uses_in_memory_cache(tmp_path):
    t = _make_step3(tmp_path)
    t.url_meta["666"] = {"tag": ["cached"], "like": 7, "pagecount": 2, "img_url": "v"}

    meta = t._get_meta("666")
    assert meta["tag"] == ["cached"]


def test_get_meta_returns_empty_when_not_found(tmp_path):
    t = _make_step4(tmp_path)
    assert t._get_meta("nonexistent") == {}


# ── 3. _maybe_flush_exist_pid marks DB immediately ───────────────────────────

def test_maybe_flush_exist_pid_marks_db(tmp_path):
    t = _make_step4(tmp_path)
    t.exist_pid = set()

    t._maybe_flush_exist_pid("12345")

    assert t._metadata_db.is_downloaded("12345") is True
    assert "12345" in t.exist_pid


def test_maybe_flush_exist_pid_no_db_does_not_raise(tmp_path):
    t = _make_step4(tmp_path)
    t._metadata_db = None
    t.exist_pid = set()

    t._maybe_flush_exist_pid("99999")  # must not raise
    assert "99999" in t.exist_pid


# ── 4. _persist_url_meta writes to DB only (no JSON) ─────────────────────────

def test_step4_persist_url_meta_writes_db_no_json(tmp_path):
    t = _make_step4(tmp_path)
    t.url_meta = {"777": {"tag": ["z"], "like": 9, "pagecount": 3, "img_url": "w"}}

    t._persist_url_meta()

    assert not (tmp_path / "all_url_meta.json").exists()
    meta = t._metadata_db.get_meta("777")
    assert meta is not None
    assert meta["tag"] == ["z"]


# ── 5. _persist_step3_url_meta writes to DB only (no JSON) ───────────────────

def test_step3_persist_url_meta_writes_db_no_json(tmp_path):
    t = _make_step3(tmp_path)
    t.url_meta = {"888": {"tag": ["q"], "like": 4, "pagecount": 1, "img_url": "x"}}

    t._persist_step3_url_meta()

    assert not (tmp_path / "all_url_meta.json").exists()
    meta = t._metadata_db.get_meta("888")
    assert meta is not None
    assert meta["tag"] == ["q"]


# ── 6. run_actions._backup_db calls backup_file on .db ───────────────────────

def test_run_actions_backup_db_called_on_run_step(tmp_path):
    from app.gui.run_actions import RunController
    from app.core.metadata_db import MetadataDB, DB_FILENAME

    # Ensure the DB file exists so the isfile guard passes
    MetadataDB(str(tmp_path)).meta_count()

    rc = RunController.__new__(RunController)
    rc._event_q = Queue()
    rc._stats_collector = None
    rc._run_all_mode = False

    # Patch throttle helpers: no prior snapshot date → backup runs; write is a no-op.
    with patch("app.gui.run_actions._data_path", return_value=str(tmp_path) + os.sep), \
         patch("app.gui.run_actions._read_event_log_cfg", return_value={}), \
         patch("app.gui.run_actions._write_event_log_cfg"), \
         patch.object(MetadataDB, "backup_db", return_value=True) as mock_backup, \
         patch.object(rc, "_start_step"):
        rc.run_step(4)

    mock_backup.assert_called_once()


def test_run_actions_backup_db_called_on_run_all(tmp_path):
    from app.gui.run_actions import RunController
    from app.core.metadata_db import MetadataDB, DB_FILENAME

    MetadataDB(str(tmp_path)).meta_count()

    rc = RunController.__new__(RunController)
    rc._event_q = Queue()
    rc._stats_collector = None
    rc._run_all_mode = False

    # Patch throttle helpers: no prior snapshot date → backup runs; write is a no-op.
    with patch("app.gui.run_actions._data_path", return_value=str(tmp_path) + os.sep), \
         patch("app.gui.run_actions._read_event_log_cfg", return_value={}), \
         patch("app.gui.run_actions._write_event_log_cfg"), \
         patch.object(MetadataDB, "backup_db", return_value=True) as mock_backup, \
         patch.object(rc, "_start_step"):
        rc.run_all()

    mock_backup.assert_called_once()
