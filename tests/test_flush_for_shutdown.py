"""Tests for the synchronous flush_for_shutdown hooks used by window-close.

The hook MUST flush any in-flight url_meta to SQLite and close the connection
so the WAL is checkpointed before flet_app.py calls os._exit(0).
A regression here would mean closing the window during Step 4 silently
drops the most-recently-fetched metadata.
SQLite is now the primary store — JSON is no longer written on flush.
"""
from pathlib import Path
import sys
import threading
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB
from app.core.thread_download import download_thread
from app.core.thread_url_fetch import get_img_url_thread
from app.core.thread_pid_scan import get_pixiv_author_imgID_Thread


# ── thread_download ─────────────────────────────────────────────────────────

def test_step4_flush_persists_url_meta_to_json(tmp_path):
    t = download_thread.__new__(download_thread)
    t.url_meta = {"100": {"tag": ["a"], "like": 1, "pagecount": 1, "img_url": "u"}}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t.path = str(tmp_path)
    t._url_meta_lock = threading.RLock()
    t._metadata_db = MetadataDB(str(tmp_path))

    t.flush_for_shutdown()

    # SQLite is now primary — verify data landed in DB, not JSON
    db2 = MetadataDB(str(tmp_path))
    meta = db2.get_meta("100")
    assert meta is not None
    assert meta["tag"] == ["a"]
    assert meta["like"] == 1


def test_step4_flush_closes_metadata_db(tmp_path):
    t = download_thread.__new__(download_thread)
    t.url_meta = {}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t.path = str(tmp_path)
    t._url_meta_lock = threading.RLock()
    t._metadata_db = MetadataDB(str(tmp_path))
    t._metadata_db.upsert_meta("1", tags=["a"])  # ensure connection is open

    t.flush_for_shutdown()
    # Re-opening from disk works because the close ran a clean checkpoint.
    db2 = MetadataDB(str(tmp_path))
    assert db2.get_meta("1")["tag"] == ["a"]


def test_step4_flush_silent_when_db_unset(tmp_path):
    t = download_thread.__new__(download_thread)
    t.url_meta = {}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t.path = str(tmp_path)
    t._url_meta_lock = threading.RLock()
    t._metadata_db = None
    # Must not raise
    t.flush_for_shutdown()


# ── thread_url_fetch ────────────────────────────────────────────────────────

def test_step3_flush_persists_url_meta_and_closes_db(tmp_path):
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.url_meta = {"42": {"tag": ["b"], "like": 5, "pagecount": 2, "img_url": "v"}}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t.path = str(tmp_path)
    t._q = Queue()
    t._metadata_db = MetadataDB(str(tmp_path))

    t.flush_for_shutdown()

    # SQLite is now primary — verify data landed in DB, not JSON
    db2 = MetadataDB(str(tmp_path))
    meta = db2.get_meta("42")
    assert meta is not None
    assert meta["tag"] == ["b"]
    assert meta["like"] == 5


def test_step3_flush_silent_when_db_unset(tmp_path):
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.url_meta = {}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t.path = str(tmp_path)
    t._q = Queue()
    t._metadata_db = None
    # Must not raise even with no DB
    t.flush_for_shutdown()


# ── thread_pid_scan ─────────────────────────────────────────────────────────

def test_step2_flush_closes_db_and_keeps_data_visible(tmp_path):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.path = str(tmp_path)
    t._metadata_db = MetadataDB(str(tmp_path))
    # Canonical sentinel write — visible to v_closed_artworks / is_downloaded.
    t._metadata_db.import_downloaded_set(["99"])

    t.flush_for_shutdown()
    # Re-opening sees the persisted data — proves the close checkpoint ran.
    db2 = MetadataDB(str(tmp_path))
    assert db2.is_downloaded("99") is True


def test_step2_flush_silent_when_db_unset(tmp_path):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.path = str(tmp_path)
    t._metadata_db = None
    # Must not raise
    t.flush_for_shutdown()
