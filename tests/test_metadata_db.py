"""Tests for app.core.metadata_db SQLite layer."""
from pathlib import Path
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB


def test_creates_db_file(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_meta("1234")
    assert (tmp_path / "metadata.sqlite3").exists()
    db.close()


def test_upsert_and_read_meta(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_meta(
        "12345",
        tags=["R-18", "オリジナル"],
        like_count=5000,
        page_count=3,
        img_url="https://i.pximg.net/img-original/img/.../12345_p.png",
        requires_cookie=True,
        updated_at="2026-05-01T12:00:00",
    )
    out = db.get_meta("12345")
    assert out is not None
    assert out["tag"] == ["R-18", "オリジナル"]
    assert out["like"] == 5000
    assert out["pagecount"] == 3
    assert out["requires_cookie"] is True
    assert out["updated_at"] == "2026-05-01T12:00:00"
    db.close()


def test_get_meta_returns_upload_date(tmp_path):
    """get_meta() must surface upload_date so the rescrape-window staleness
    check in thread_url_fetch can compare it against now. Regression: before
    this was added, the SELECT omitted upload_date and the rescrape feature
    silently no-op'd for every PID that lived only in the DB (i.e. all PIDs
    after the first run, since the in-memory url_meta cache starts empty)."""
    db = MetadataDB(str(tmp_path))
    db.upsert_artwork(
        "98765",
        page_count=2,
        like_count=100,
        tags=["x"],
        img_url_template="https://i.pximg.net/.../98765_p.jpg",
        upload_date="2024-01-15T12:30:00+09:00",
        meta_updated_at="2026-05-01T00:00:00",
    )
    out = db.get_meta("98765")
    assert out is not None
    assert out["upload_date"] == "2024-01-15T12:30:00+09:00"
    db.close()


def test_upsert_partial_update_preserves_other_fields(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_meta("1", tags=["a"], like_count=10, page_count=1, img_url="u")
    # Update only like_count; tags/page_count/img_url must persist.
    db.upsert_meta("1", like_count=20)
    out = db.get_meta("1")
    assert out["tag"] == ["a"]
    assert out["like"] == 20
    assert out["pagecount"] == 1
    assert out["img_url"] == "u"
    db.close()


def test_get_meta_missing_returns_none(tmp_path):
    db = MetadataDB(str(tmp_path))
    assert db.get_meta("9999") is None
    db.close()


def test_has_meta(tmp_path):
    db = MetadataDB(str(tmp_path))
    assert db.has_meta("1") is False
    db.upsert_meta("1", tags=["x"])
    assert db.has_meta("1") is True
    db.close()


def test_pid_normalization(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_meta("12345_p0", tags=["a"])
    # Reading with the bare PID, with page suffix, or with the numeric form
    # all resolve to the same row.
    assert db.get_meta("12345") is not None
    assert db.get_meta("12345_p0") is not None
    assert db.get_meta(12345) is not None
    db.close()


def test_empty_pid_is_noop(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.upsert_meta("", tags=["x"])
    db.upsert_meta(None, tags=["x"])
    assert db.meta_count() == 0
    db.close()


def test_mark_and_query_downloaded(tmp_path):
    db = MetadataDB(str(tmp_path))
    assert db.is_downloaded("1") is False
    # Canonical close: sentinel artwork row (no pages required).
    db.import_downloaded_set(["1"])
    assert db.is_downloaded("1") is True
    db.import_downloaded_set(["2"])
    assert db.downloaded_count() == 2
    assert db.downloaded_set() == {"1", "2"}
    db.close()


def test_import_meta_dict_round_trip(tmp_path):
    db = MetadataDB(str(tmp_path))
    src = {
        "100": {"tag": ["a", "b"], "like": 10, "pagecount": 2, "img_url": "u1",
                "requires_cookie": False},
        "200": {"tag": ["c"], "like": 20, "pagecount": 1, "img_url": "u2",
                "requires_cookie": True, "updated_at": "2026-01-01T00:00:00"},
    }
    inserted = db.import_meta_dict(src)
    assert inserted == 2
    out = db.export_meta_dict()
    assert set(out.keys()) == {"100", "200"}
    assert out["100"]["tag"] == ["a", "b"]
    assert out["200"]["requires_cookie"] is True
    db.close()


def test_import_meta_dict_handles_garbage(tmp_path):
    db = MetadataDB(str(tmp_path))
    # Non-dict entries and empty pids are skipped, real entries succeed.
    src = {
        "": {"tag": ["x"]},
        "300": "not-a-dict",
        "400": {"tag": ["ok"], "like": 1, "pagecount": 1, "img_url": "u"},
    }
    inserted = db.import_meta_dict(src)
    assert inserted == 1
    assert db.has_meta("400") is True


def test_import_downloaded_set(tmp_path):
    db = MetadataDB(str(tmp_path))
    inserted = db.import_downloaded_set(["1", "2", "3_p1", "", None])
    assert inserted == 3
    assert db.downloaded_set() == {"1", "2", "3"}
    db.close()


def test_thread_safe_writes(tmp_path):
    db = MetadataDB(str(tmp_path))

    def writer(start, end):
        for i in range(start, end):
            db.upsert_meta(str(i), tags=[f"t{i}"], like_count=i)

    threads = [
        threading.Thread(target=writer, args=(0, 100)),
        threading.Thread(target=writer, args=(100, 200)),
        threading.Thread(target=writer, args=(200, 300)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert db.meta_count() == 300
    # Spot-check a few rows
    assert db.get_meta("0")["like"] == 0
    assert db.get_meta("250")["like"] == 250
    db.close()


def test_export_with_no_data_returns_empty(tmp_path):
    db = MetadataDB(str(tmp_path))
    assert db.export_meta_dict() == {}
    db.close()
