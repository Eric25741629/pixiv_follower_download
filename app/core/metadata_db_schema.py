"""Canonical SQLite DDL for the metadata store.

Extracted verbatim from ``metadata_db.py`` (file-size refactor) so the
table / view / index definitions live in one cohesive place. ``MetadataDB._conn``
runs this as a single ``executescript`` on first open; every statement is
idempotent (``CREATE ... IF NOT EXISTS`` / ``DROP ... IF EXISTS``), so re-running
it against an existing DB is a no-op.
"""
from __future__ import annotations

_SCHEMA = """
-- Phase 8: drop legacy tables on first open (idempotent after that).
DROP TABLE IF EXISTS pids;
DROP TABLE IF EXISTS downloaded;
DROP TABLE IF EXISTS pending_urls;
DROP TABLE IF EXISTS pending_pids;

-- Canonical schema: artworks + pages.
CREATE TABLE IF NOT EXISTS artworks (
    pid              TEXT PRIMARY KEY,
    discovered_at    TEXT NOT NULL,
    page_count       INTEGER,
    like_count       INTEGER,
    tags             TEXT,
    img_url_template TEXT,
    requires_cookie  INTEGER,
    meta_updated_at  TEXT,
    revoked_at       TEXT,
    upload_date      TEXT,
    create_date      TEXT,
    user_id          TEXT,
    user_name        TEXT
);
CREATE TABLE IF NOT EXISTS pages (
    pid               TEXT NOT NULL,
    page_index        INTEGER NOT NULL,
    status            TEXT NOT NULL,
    url               TEXT,
    file_path         TEXT,
    file_size         INTEGER,
    downloaded_at     TEXT,
    last_attempted_at TEXT,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    failure_reason    TEXT,
    PRIMARY KEY (pid, page_index)
);
CREATE INDEX IF NOT EXISTS idx_artworks_meta_updated_at
    ON artworks(meta_updated_at);
CREATE INDEX IF NOT EXISTS idx_artworks_revoked_at
    ON artworks(revoked_at);
CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status);
CREATE INDEX IF NOT EXISTS idx_pages_pid    ON pages(pid);

-- Convenience views — empty until migration backfills the tables.
CREATE VIEW IF NOT EXISTS v_pending_artworks AS
    SELECT pid FROM artworks
    WHERE meta_updated_at IS NULL AND revoked_at IS NULL;

CREATE VIEW IF NOT EXISTS v_pending_pages AS
    SELECT pid, page_index, url FROM pages WHERE status = 'pending';

-- A PID is "complete" when the count of downloaded pages reaches
-- artworks.page_count. NULL page_count means we haven't yet fetched meta
-- for it, so completion can't be asserted — those PIDs are excluded.
CREATE VIEW IF NOT EXISTS v_complete_artworks AS
    SELECT a.pid
    FROM artworks a
    JOIN (
        SELECT pid, COUNT(*) AS done
        FROM pages WHERE status = 'downloaded' GROUP BY pid
    ) c ON c.pid = a.pid
    WHERE a.page_count IS NOT NULL AND c.done >= a.page_count;

-- A PID is "closed for processing" — Step 4 will not try to download it.
-- Three independent ways to land here:
--   1. revoked_at IS NOT NULL  (Pixiv 404'd it OR migration marked it
--                              for any other reason it should be skipped)
--   2. v_complete_artworks      (full meta + every page on disk)
--   3. legacy sentinel + no pending pages
--                              (migration imported from exist_pid.json,
--                              no evidence of work to do)
CREATE VIEW IF NOT EXISTS v_closed_artworks AS
    SELECT pid FROM artworks WHERE revoked_at IS NOT NULL
    UNION
    SELECT pid FROM v_complete_artworks
    UNION
    SELECT a.pid FROM artworks a
    WHERE a.meta_updated_at = '0001-01-01 00:00:00'
      AND NOT EXISTS (
          SELECT 1 FROM pages p WHERE p.pid = a.pid AND p.status = 'pending'
      );
"""


# Shared ON CONFLICT upsert tail for the ``artworks`` table (COALESCE = only
# fill NULLs, never clobber known values). ``revoked_at`` is NOT included:
# upsert_artwork appends it, the legacy-migration import deliberately omits it.
ARTWORK_UPSERT_SET_CLAUSE = (
    "ON CONFLICT(pid) DO UPDATE SET "
    "page_count       = COALESCE(excluded.page_count, artworks.page_count), "
    "like_count       = COALESCE(excluded.like_count, artworks.like_count), "
    "tags             = COALESCE(excluded.tags, artworks.tags), "
    "img_url_template = COALESCE(excluded.img_url_template, artworks.img_url_template), "
    "requires_cookie  = COALESCE(excluded.requires_cookie, artworks.requires_cookie), "
    "meta_updated_at  = COALESCE(excluded.meta_updated_at, artworks.meta_updated_at), "
    "upload_date      = COALESCE(excluded.upload_date, artworks.upload_date), "
    "create_date      = COALESCE(excluded.create_date, artworks.create_date), "
    "user_id          = COALESCE(excluded.user_id, artworks.user_id), "
    "user_name        = COALESCE(excluded.user_name, artworks.user_name)"
)
