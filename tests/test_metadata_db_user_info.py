"""Tests for the user_id / user_name / upload_date / create_date columns
added to the artworks table, plus the idempotent ALTER TABLE migration."""
from pathlib import Path
import sqlite3
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import DB_FILENAME, MetadataDB


def _open(tmp_path):
    return MetadataDB(str(tmp_path), event_log=None)


def test_upsert_artwork_persists_new_fields(tmp_path):
    db = _open(tmp_path)
    db.upsert_artwork(
        "100",
        page_count=2,
        like_count=42,
        tags=["a", "b"],
        img_url_template="http://x/{p}.jpg",
        meta_updated_at="2026-05-24 12:00:00",
        upload_date="2026-05-20T09:00:00+09:00",
        create_date="2026-05-20T08:59:00+09:00",
        user_id="111",
        user_name="ArtistA",
    )
    row = db.get_artwork("100")
    assert row["upload_date"] == "2026-05-20T09:00:00+09:00"
    assert row["create_date"] == "2026-05-20T08:59:00+09:00"
    assert row["user_id"] == "111"
    assert row["user_name"] == "ArtistA"
    # fetched_at is the alias for meta_updated_at
    assert row["fetched_at"] == "2026-05-24 12:00:00"
    assert row["meta_updated_at"] == "2026-05-24 12:00:00"


def test_upsert_artwork_partial_update_keeps_existing_fields(tmp_path):
    """COALESCE-on-conflict: a second call without a field must not erase it."""
    db = _open(tmp_path)
    db.upsert_artwork("100", user_id="111", user_name="ArtistA",
                      upload_date="2026-05-20T09:00:00+09:00")
    db.upsert_artwork("100", like_count=99)  # no user_id / user_name passed
    row = db.get_artwork("100")
    assert row["user_id"] == "111"
    assert row["user_name"] == "ArtistA"
    assert row["upload_date"] == "2026-05-20T09:00:00+09:00"
    assert row["like_count"] == 99


def test_upsert_artworks_records_user_id_on_discovery(tmp_path):
    db = _open(tmp_path)
    n = db.upsert_artworks(["100", "200"], user_id="111")
    assert n == 2
    assert db.get_artwork("100")["user_id"] == "111"
    assert db.get_artwork("200")["user_id"] == "111"


def test_upsert_artworks_backfills_user_id_for_existing_rows(tmp_path):
    """A PID discovered earlier without user_id gets its author filled in by
    a later discovery pass (e.g. when an existing exist_pid.json is migrated
    and then Step 2 re-scans the same artist)."""
    db = _open(tmp_path)
    db.upsert_artworks(["100"])  # no user_id yet
    assert db.get_artwork("100")["user_id"] is None
    db.upsert_artworks(["100", "200"], user_id="111")
    assert db.get_artwork("100")["user_id"] == "111"
    assert db.get_artwork("200")["user_id"] == "111"


def test_upsert_artworks_keeps_first_writer_user_id(tmp_path):
    """If a PID already has user_id, a later discovery pass with a different
    user_id must not overwrite it (first writer wins). Defends against the
    rare case where two artists both list the same artwork in their PID set."""
    db = _open(tmp_path)
    db.upsert_artworks(["100"], user_id="111")
    db.upsert_artworks(["100"], user_id="222")
    assert db.get_artwork("100")["user_id"] == "111"


def test_upsert_pending_pids_accepts_tuples_with_user_id(tmp_path):
    db = _open(tmp_path)
    db.upsert_pending_pids([("100", "111"), ("200", "222"), "300"], user_id="999")
    # Per-PID user_id beats the shared keyword, and unannotated PIDs pick up
    # the keyword fallback.
    assert db.get_artwork("100")["user_id"] == "111"
    assert db.get_artwork("200")["user_id"] == "222"
    assert db.get_artwork("300")["user_id"] == "999"


def test_upsert_pending_pids_flat_pid_list_still_works(tmp_path):
    """Legacy callers passing a flat list of strings keep working."""
    db = _open(tmp_path)
    db.upsert_pending_pids(["100", "200"])
    # No user_id means NULL in artworks; rows still exist.
    assert db.get_artwork("100")["user_id"] is None
    assert db.get_artwork("200")["user_id"] is None


def test_alter_table_migration_swallows_duplicate_column():
    """The known idempotent case: `ALTER TABLE ... ADD COLUMN` against an
    existing column raises ``OperationalError("duplicate column name: X")``
    and **must** be swallowed so repeat startups don't fail."""
    import sqlite3
    from app.core.metadata_db import MetadataDB as _DB

    class FakeConn:
        def execute(self, _sql):
            raise sqlite3.OperationalError("duplicate column name: upload_date")

    # Should not raise.
    _DB._apply_artwork_column_migrations(FakeConn())


def test_alter_table_migration_propagates_non_duplicate_errors():
    """All other ``OperationalError`` variants (DB locked, type mismatch,
    file-perm failures, etc.) must propagate so startup fails loudly rather
    than silently running on a half-migrated DB."""
    import sqlite3
    from app.core.metadata_db import MetadataDB as _DB

    class FakeConn:
        def execute(self, _sql):
            raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _DB._apply_artwork_column_migrations(FakeConn())


def test_alter_table_migration_is_idempotent(tmp_path):
    """Opening the DB twice must not fail with duplicate-column errors."""
    db1 = _open(tmp_path)
    db1.upsert_artwork("100", user_id="111")
    db1.close()
    db2 = _open(tmp_path)  # re-open: ALTER runs again, must be no-op
    assert db2.get_artwork("100")["user_id"] == "111"
    db2.close()


def test_alter_table_migration_promotes_old_schema(tmp_path):
    """Simulate an old DB that predates the 4 new columns: create the table
    by hand without them, then open via MetadataDB and confirm the ALTERs ran."""
    db_path = tmp_path / DB_FILENAME
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE artworks (
            pid              TEXT PRIMARY KEY,
            discovered_at    TEXT NOT NULL,
            page_count       INTEGER,
            like_count       INTEGER,
            tags             TEXT,
            img_url_template TEXT,
            requires_cookie  INTEGER,
            meta_updated_at  TEXT,
            revoked_at       TEXT
        );
        INSERT INTO artworks (pid, discovered_at) VALUES ('100', '2026-01-01 00:00:00');
        """
    )
    conn.close()
    db = _open(tmp_path)
    # Migration applied — we can now write to the new columns.
    db.upsert_artwork("100", user_id="111", user_name="ArtistA",
                      upload_date="2026-05-20T09:00:00+09:00")
    row = db.get_artwork("100")
    assert row["user_id"] == "111"
    assert row["user_name"] == "ArtistA"
    assert row["upload_date"] == "2026-05-20T09:00:00+09:00"
    db.close()


def test_import_meta_dict_carries_user_info(tmp_path):
    """The legacy all_url_meta.json import path also accepts the new fields."""
    db = _open(tmp_path)
    db.import_meta_dict({
        "100": {
            "tag": ["a"],
            "like": 50,
            "pagecount": 1,
            "img_url": "http://x/100_p.jpg",
            "updated_at": "2026-05-24 11:00:00",
            "upload_date": "2026-05-20T09:00:00+09:00",
            "create_date": "2026-05-20T08:55:00+09:00",
            "user_id": "111",
            "user_name": "ArtistA",
        }
    })
    row = db.get_artwork("100")
    assert row["upload_date"] == "2026-05-20T09:00:00+09:00"
    assert row["create_date"] == "2026-05-20T08:55:00+09:00"
    assert row["user_id"] == "111"
    assert row["user_name"] == "ArtistA"
