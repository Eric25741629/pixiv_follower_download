"""SQLite-backed metadata cache.

Canonical tables:

    artworks (pid, discovered_at, page_count, like_count, tags,
              img_url_template, requires_cookie, meta_updated_at,
              revoked_at)
            — one row per PID the downloader has seen.
    pages   (pid, page_index, status, url, file_path, file_size,
             downloaded_at, last_attempted_at, attempt_count,
             failure_reason)
            — one row per (PID, page) tuple, status ∈ {pending,
            downloaded, failed, revoked}.

Legacy tables (``pids``, ``downloaded``, ``pending_urls``,
``pending_pids``) were dropped in Phase 8. The schema migration runs
``DROP TABLE IF EXISTS`` on first open so any existing DB is cleaned up
automatically.

Concurrency: SQLite is opened in WAL mode, which lets the four worker
threads (Steps 2/3/4) share a connection-per-thread without contention.
``MetadataDB`` is intentionally tiny — call sites can drop straight in
without learning a new ORM.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterable
import contextlib

DB_FILENAME = "metadata.sqlite3"


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

# Status constants — keep callers literal-free. Set, not enum, so a typo
# raises KeyError immediately instead of silently mis-comparing.
PAGE_STATUS_PENDING = "pending"
PAGE_STATUS_DOWNLOADED = "downloaded"
PAGE_STATUS_FAILED = "failed"
PAGE_STATUS_REVOKED = "revoked"
_VALID_PAGE_STATUSES = frozenset({
    PAGE_STATUS_PENDING, PAGE_STATUS_DOWNLOADED,
    PAGE_STATUS_FAILED, PAGE_STATUS_REVOKED,
})


class MetadataDB:
    """Thread-safe SQLite cache for Pixiv metadata + downloaded-PID set."""

    def __init__(self, base_path: str, *, event_log=None):
        self._base = str(base_path or "").strip()
        self._path = os.path.join(self._base, DB_FILENAME)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._initialized = False
        self._event_log = event_log

    def _emit(self, kind: str, **fields) -> None:
        """Forward to the attached event log, swallowing all errors so a
        log-write failure never blocks a DB write (degraded mode)."""
        if self._event_log is None:
            return
        try:
            self._event_log.emit(kind, **fields)
        except Exception:
            import logging
            logging.getLogger("pixiv.metadata_db").debug(
                "event log emit failed for kind=%s", kind, exc_info=True,
            )

    # ── connection management ─────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        """Return a per-thread connection; create + initialize on first use."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        if self._base and not os.path.isdir(self._base):
            os.makedirs(self._base, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=268435456")   # 256 MB memory-mapped I/O
        conn.execute("PRAGMA cache_size=-65536")      # 64 MB page cache
        with self._lock:
            if not self._initialized:
                conn.executescript(_SCHEMA)
                self._apply_artwork_column_migrations(conn)
                self._initialized = True
        self._local.conn = conn  # cached only after successful init
        return conn

    @staticmethod
    def _apply_artwork_column_migrations(conn: sqlite3.Connection) -> None:
        """Idempotent ``ALTER TABLE artworks ADD COLUMN`` migrations.

        Each column added after the original schema lives here. SQLite raises
        ``OperationalError`` with message ``duplicate column name: <col>`` when
        the column already exists, which is the only OperationalError this
        migration is expected to encounter — every other error (DB locked,
        wrong column type, file-perm issue) means something is genuinely
        broken and **must** propagate so startup fails loudly rather than
        silently running on a half-migrated DB.

        New deployments hit ``CREATE TABLE IF NOT EXISTS`` in ``_SCHEMA``
        which already includes these columns; the ALTERs then fail with
        "duplicate column" on the first call and are no-ops thereafter.
        """
        columns = (
            "upload_date TEXT",
            "create_date TEXT",
            "user_id     TEXT",
            "user_name   TEXT",
        )
        for col_def in columns:
            try:
                conn.execute(f"ALTER TABLE artworks ADD COLUMN {col_def}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    def backup_db(self, max_history: int = 3) -> bool:
        """Create a WAL-safe backup using SQLite's built-in backup API.

        shutil.copy2 is unsafe for WAL-mode databases: it copies only the main
        file and misses any data still in the .sqlite3-wal sidecar.
        sqlite3.Connection.backup() is atomic and handles concurrent readers.
        After backup, old copies beyond *max_history* are pruned.
        """
        import datetime
        try:
            if not os.path.isfile(self._path):
                return False
            hist_dir = os.path.join(self._base, "history")
            os.makedirs(hist_dir, exist_ok=True)
            base = os.path.basename(self._path)
            ts = datetime.datetime.now().strftime("%Y%m%d")
            dst_path = os.path.join(hist_dir, f"{base}.{ts}")
            if os.path.exists(dst_path):
                idx = 1
                while os.path.exists(f"{dst_path}.{idx}"):
                    idx += 1
                dst_path = f"{dst_path}.{idx}"
            src = sqlite3.connect(self._path, timeout=10.0, isolation_level=None)
            try:
                # TRUNCATE (vs PASSIVE) also physically shrinks the -wal file back
                # to zero. Safe here: dedicated short-lived connection invoked at
                # an idle point (run start, before workers); if a writer holds the
                # lock it silently no-ops rather than erroring.
                src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                dst = sqlite3.connect(dst_path, timeout=10.0, isolation_level=None)
                try:
                    src.backup(dst)
                    # Durably flush the snapshot before any caller prunes the
                    # pre-snapshot event log against it (STR3 compaction).
                    with contextlib.suppress(Exception):
                        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    dst.close()
            finally:
                src.close()
            try:
                _fd = os.open(dst_path, os.O_RDONLY)
                try:
                    os.fsync(_fd)
                finally:
                    os.close(_fd)
            except OSError:
                pass
            # Prune old backups — keep the most recent max_history copies
            files = sorted(
                [f for f in os.listdir(hist_dir) if f.startswith(base + ".")],
                key=lambda f: os.path.getmtime(os.path.join(hist_dir, f)),
                reverse=True,
            )
            for old in files[max_history:]:
                with contextlib.suppress(Exception):
                    os.remove(os.path.join(hist_dir, old))
            try:
                size = os.path.getsize(dst_path)
            except OSError:
                size = 0
            self._emit("snapshot", backup_path=os.path.relpath(dst_path, self._base), db_size=size)
            return True
        except Exception:
            return False

    def _bulk_write(self, sql: str, rows: list) -> None:
        """Execute a bulk write in a single atomic block.

        ``isolation_level=None`` means autocommit; without grouping the
        executemany() in one transaction each row would be its own
        BEGIN/COMMIT, which is slow for thousands of inserts. ``SAVEPOINT``
        is used instead of ``BEGIN``/``COMMIT`` so callers can wrap
        ``_bulk_write`` calls inside a larger transaction (e.g. the
        migration script's outer SAVEPOINT for dry-run rollback).
        """
        if not rows:
            return
        with self._lock:
            conn = self._conn()
            conn.execute("SAVEPOINT bw")
            try:
                conn.executemany(sql, rows)
                conn.execute("RELEASE bw")
            except Exception:
                try:
                    conn.execute("ROLLBACK TO bw")
                    conn.execute("RELEASE bw")
                except Exception:
                    pass
                raise

    # ── pid metadata ──────────────────────────────────────────────────────

    def upsert_meta(
        self,
        pid: str,
        *,
        tags: list | None = None,
        like_count: int | None = None,
        page_count: int | None = None,
        img_url: str | None = None,
        requires_cookie: bool | None = None,
        updated_at: str | None = None,
    ) -> None:
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return
        import datetime
        stamp = updated_at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.upsert_artwork(
            pid_key,
            page_count=page_count, like_count=like_count,
            tags=tags, img_url_template=img_url,
            requires_cookie=requires_cookie,
            meta_updated_at=stamp,
        )

    def get_meta(self, pid: str) -> dict | None:
        """Look up artwork meta in the new schema, returning the legacy
        ``{tag, like, pagecount, img_url, requires_cookie, updated_at,
        upload_date}`` dict shape that existing callers expect.

        Returns ``None`` only when no ``artworks`` row exists for the PID
        AND no meta has ever been recorded — a row that exists but lacks
        meta (``meta_updated_at IS NULL``) still returns ``None`` so the
        caller's "needs network fetch" branch fires.

        ``upload_date`` is the Pixiv-reported ISO-8601 upload timestamp;
        consumers use it for the rescrape-window staleness check (see
        ``thread_url_fetch.get_img_url_thread._is_within_rescrape_window``).
        """
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return None
        cur = self._conn().execute(
            "SELECT tags, like_count, page_count, img_url_template, "
            "requires_cookie, meta_updated_at, upload_date "
            "FROM artworks WHERE pid = ? AND meta_updated_at IS NOT NULL",
            (pid_key,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        tags_blob, like, pages, img, rc, updated, upload = row
        try:
            tags = json.loads(tags_blob) if tags_blob else []
        except Exception:
            tags = []
        return {
            "tag": tags,
            "like": like,
            "pagecount": pages,
            "img_url": img,
            "requires_cookie": None if rc is None else bool(rc),
            "updated_at": updated,
            "upload_date": upload,
        }

    def has_meta(self, pid: str) -> bool:
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return False
        cur = self._conn().execute(
            "SELECT 1 FROM artworks WHERE pid = ? "
            "AND meta_updated_at IS NOT NULL LIMIT 1", (pid_key,)
        )
        return cur.fetchone() is not None

    def meta_count(self) -> int:
        """Count artworks for which meta has ever been recorded."""
        cur = self._conn().execute(
            "SELECT COUNT(*) FROM artworks WHERE meta_updated_at IS NOT NULL"
        )
        return int(cur.fetchone()[0])

    # ── downloaded-pid set ────────────────────────────────────────────────

    def mark_downloaded(self, pid: str, *, downloaded_at: str | None = None) -> None:
        """Mark a PID as closed via a sentinel artwork row.

        Redirected to :meth:`import_downloaded_set` in Phase 8 — the
        legacy ``downloaded`` table has been dropped.
        """
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return
        self.import_downloaded_set([pid_key])

    def is_downloaded(self, pid: str) -> bool:
        """PID-level closed check — reads from ``v_closed_artworks``.

        Returns True for any PID that is fully downloaded, revoked, or
        imported as a legacy sentinel (via :meth:`import_downloaded_set`).
        For strict per-page completion use :meth:`is_pid_complete`.
        Phase 8 may rename or remove this wrapper.
        """
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return False
        cur = self._conn().execute(
            "SELECT 1 FROM v_closed_artworks WHERE pid = ? LIMIT 1", (pid_key,)
        )
        return cur.fetchone() is not None

    def downloaded_set(self) -> set[str]:
        """Return the set of all closed PIDs (``v_closed_artworks``)."""
        return self.closed_artwork_set()

    def downloaded_count(self) -> int:
        """Count all closed PIDs (``v_closed_artworks``)."""
        cur = self._conn().execute("SELECT COUNT(*) FROM v_closed_artworks")
        return int(cur.fetchone()[0])

    # ── bulk migration helpers ────────────────────────────────────────────

    def import_meta_dict(self, meta: dict) -> int:
        """Bulk-insert from the legacy all_url_meta.json shape into ``artworks``.

        ``meta_updated_at`` falls back to ``datetime('now')`` when the
        caller's entry didn't carry a timestamp — calling import_meta_dict
        always means "we have meta", so a NULL would wrongly hide the row
        from get_meta() (which filters meta_updated_at IS NOT NULL).
        Returns the number of rows attempted.
        """
        if not isinstance(meta, dict) or not meta:
            return 0
        artworks_rows = []
        for pid, entry in meta.items():
            pid_key = self._coerce_pid(pid)
            if not pid_key or not isinstance(entry, dict):
                continue
            artworks_rows.append(self._build_artwork_row(pid_key, entry))
        if not artworks_rows:
            return 0
        self._bulk_write(
            "INSERT INTO artworks (pid, discovered_at, page_count, like_count, "
            "tags, img_url_template, requires_cookie, meta_updated_at, "
            "upload_date, create_date, user_id, user_name) "
            "VALUES (?, COALESCE(?, datetime('now')), ?, ?, ?, ?, ?, "
            "COALESCE(?, datetime('now')), ?, ?, ?, ?) "
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
            "user_name        = COALESCE(excluded.user_name, artworks.user_name)",
            artworks_rows,
        )
        return len(artworks_rows)

    @staticmethod
    def _build_artwork_row(pid_key, entry):
        """Translate one (pid, entry) into a row tuple for the artworks table.

        Mirrors :meth:`_build_meta_row` but targets the new column names.
        """
        tags = entry.get("tag")
        if tags is None:
            tags = entry.get("tags", [])
        tags_blob = json.dumps(
            list(tags) if isinstance(tags, list) else [],
            ensure_ascii=False,
        )
        rc = entry.get("requires_cookie")
        rc_int = None if rc is None else (1 if rc else 0)
        updated = entry.get("updated_at") or entry.get("checked_at")
        upload = entry.get("upload_date")
        create = entry.get("create_date")
        uid = entry.get("user_id")
        uname = entry.get("user_name")
        return (
            pid_key,
            updated,                                          # discovered_at fallback
            entry.get("pagecount") or entry.get("page_count"),
            entry.get("like"),
            tags_blob,
            entry.get("img_url"),
            rc_int,
            updated,                                          # meta_updated_at
            None if upload in (None, "") else str(upload),
            None if create in (None, "") else str(create),
            None if uid in (None, "") else str(uid),
            None if uname in (None, "") else str(uname),
        )

    def import_downloaded_set(self, pids: Iterable) -> int:
        """Bulk-import a PID set as 'closed' artworks (sentinel rows).

        Each PID becomes an ``artworks`` row with
        ``meta_updated_at = '0001-01-01 00:00:00'``, which ``v_closed_artworks``
        treats as a legacy-closed sentinel. Safe to call multiple times —
        ``INSERT OR IGNORE`` is idempotent.
        """
        artwork_rows = []
        for v in pids or ():
            pid_key = self._coerce_pid(v)
            if pid_key:
                artwork_rows.append((pid_key, "0001-01-01 00:00:00", "0001-01-01 00:00:00"))
        if not artwork_rows:
            return 0
        pids_to_emit = [r[0] for r in artwork_rows]
        self._emit(
            "artwork.imported_set",
            pids=pids_to_emit,
            discovered_at="0001-01-01 00:00:00",
            meta_updated_at="0001-01-01 00:00:00",
        )
        self._bulk_write(
            "INSERT OR IGNORE INTO artworks (pid, discovered_at, meta_updated_at) "
            "VALUES (?, ?, ?)",
            artwork_rows,
        )
        return len(artwork_rows)

    def export_meta_dict(self) -> dict:
        """Return the canonical dict shape, suitable for writing all_url_meta.json.

        Reads from ``artworks`` (only rows where meta has been fetched,
        i.e. ``meta_updated_at IS NOT NULL`` and not the sentinel value).
        """
        cur = self._conn().execute(
            "SELECT pid, tags, like_count, page_count, img_url_template, "
            "requires_cookie, meta_updated_at FROM artworks "
            "WHERE meta_updated_at IS NOT NULL "
            "AND meta_updated_at != '0001-01-01 00:00:00'"
        )
        out: dict = {}
        for pid, tags_blob, like, pages, img, rc, updated in cur.fetchall():
            try:
                tags = json.loads(tags_blob) if tags_blob else []
            except Exception:
                tags = []
            out[str(pid)] = {
                "tag": tags,
                "like": like,
                "pagecount": pages,
                "img_url": img,
                "requires_cookie": None if rc is None else bool(rc),
                "updated_at": updated,
            }
        return out

    # ── pending_urls ──────────────────────────────────────────────────────

    def upsert_pending_urls(self, entries: list) -> None:
        """Bulk-insert (url, pid) pairs into ``pages`` as status='pending'.

        URLs that cannot be parsed into a (pid, page_index) pair are
        silently dropped — they were malformed even under the old schema.
        """
        if not entries:
            return
        rows = [(str(u), str(p)) for u, p in entries if u]
        if not rows:
            return
        from app.core.pid_filesystem import parse_pid_and_page_from_url
        page_rows: list = []
        for url, pid in rows:
            parsed_pid, pidx = parse_pid_and_page_from_url(url)
            target_pid = parsed_pid or pid
            if target_pid and pidx is not None:
                page_rows.append((target_pid, pidx, PAGE_STATUS_PENDING, url, None))
        if page_rows:
            self.upsert_pages_bulk(page_rows)
            self._bulk_write(
                "INSERT OR IGNORE INTO artworks (pid, discovered_at) "
                "VALUES (?, datetime('now'))",
                [(r[0],) for r in page_rows],
            )

    def get_pending_urls(self) -> list:
        """``[(url, pid)]`` from the canonical ``v_pending_pages`` view.

        The ``url`` column may be ``None`` for rows seeded by PID-only
        workflows; callers that need a non-None URL should use
        :meth:`get_pending_urls_filtered` instead.
        """
        cur = self._conn().execute(
            "SELECT url, pid FROM v_pending_pages"
        )
        return cur.fetchall()

    def mark_url_done(self, url: str) -> None:
        """Mark the page for this URL as downloaded in ``pages``."""
        from app.core.pid_filesystem import parse_pid_and_page_from_url
        pid, pidx = parse_pid_and_page_from_url(str(url))
        if pid is not None and pidx is not None:
            self.mark_page_downloaded(pid, pidx, url=str(url))

    def mark_pages_downloaded_bulk(self, rows) -> None:
        """Bulk-mark pages as downloaded in ONE statement.

        Each row is ``(pid, page_index, url)`` (url may be ``None``, or the row
        may be a 2-tuple). Inserts the page as ``downloaded`` if absent,
        otherwise flips an existing pending/failed row to ``downloaded`` while
        preserving the original ``downloaded_at`` of already-downloaded rows.

        Emitted as a single ``pages.downloaded_bulk`` event whose replay handler
        calls back here, so crash recovery correctly turns completed pages
        ``downloaded``. (A plain ``pages.upsert_bulk`` event replays through
        INSERT OR IGNORE and would leave a pre-seeded 'pending' row stuck
        pending, silently re-queuing already-downloaded pages.)
        """
        clean: list = []
        for row in rows or ():
            try:
                pid_raw, pidx_raw, url = row
            except (TypeError, ValueError):
                try:
                    pid_raw, pidx_raw = row
                    url = None
                except (TypeError, ValueError):
                    continue
            pid_key = self._coerce_pid(pid_raw)
            if not pid_key:
                continue
            try:
                pidx = int(pidx_raw)
            except (TypeError, ValueError):
                continue
            clean.append((pid_key, pidx, url))
        if not clean:
            return
        self._emit("pages.downloaded_bulk", rows=[list(r) for r in clean])
        self._bulk_write(
            "INSERT INTO pages "
            "(pid, page_index, status, url, downloaded_at, last_attempted_at, attempt_count) "
            "VALUES (?, ?, 'downloaded', ?, datetime('now'), datetime('now'), 0) "
            "ON CONFLICT(pid, page_index) DO UPDATE SET "
            "status='downloaded', "
            "url=COALESCE(excluded.url, pages.url), "
            "downloaded_at=datetime('now'), "
            "last_attempted_at=datetime('now') "
            "WHERE pages.status != 'downloaded'",
            clean,
        )

    def mark_urls_done(self, urls) -> None:
        """Bulk-mark pages as downloaded for the given URL list (one statement,
        one ``pages.downloaded_bulk`` event)."""
        from app.core.pid_filesystem import parse_pid_and_page_from_url
        rows: list = []
        for url in urls:
            if not url:
                continue
            pid, pidx = parse_pid_and_page_from_url(str(url))
            if pid is not None and pidx is not None:
                rows.append((pid, pidx, str(url)))
        if rows:
            self.mark_pages_downloaded_bulk(rows)

    def pending_url_count(self) -> int:
        """Number of ``pages`` rows with ``status='pending'``."""
        cur = self._conn().execute(
            "SELECT COUNT(*) FROM pages WHERE status='pending'"
        )
        return int(cur.fetchone()[0])

    def get_pending_urls_filtered(self, like_min: int = 0) -> list:
        """Return [(url, pid)] for pending pages, pre-filtered in SQL.

        Reads from the new ``pages`` table; rows naturally exclude already-
        downloaded pages because they have ``status='downloaded'`` instead.
        ``like_min`` filters out artworks whose ``like_count`` is *known*
        and below the threshold — unknown likes (NULL) pass through so
        Step 4 can fetch them at runtime.

        Pages without a URL (template-unresolvable) are skipped; Step 4 has
        no way to download them without the URL. Step 3 will regenerate
        them on next run when meta is refreshed.

        Tag filtering and ``special_like_rules`` cannot be expressed
        efficiently in SQL and stay in Python (``_prepare_download_tasks``).
        """
        if like_min > 0:
            sql = """
                SELECT p.url, p.pid
                FROM pages p
                LEFT JOIN artworks a ON a.pid = p.pid
                WHERE p.status = 'pending'
                  AND p.url IS NOT NULL
                  AND (a.like_count IS NULL OR a.like_count >= ?)
            """
            cur = self._conn().execute(sql, (like_min,))
        else:
            sql = """
                SELECT p.url, p.pid
                FROM pages p
                WHERE p.status = 'pending'
                  AND p.url IS NOT NULL
            """
            cur = self._conn().execute(sql)
        return cur.fetchall()

    def url_row_count(self) -> int:
        """Total page rows in ``pages`` regardless of status.

        Replaces the old ``pending_urls`` row count. Use
        ``page_status_counts()`` for per-status breakdowns.
        """
        cur = self._conn().execute("SELECT COUNT(*) FROM pages")
        return int(cur.fetchone()[0])

    # ── pending_pids ──────────────────────────────────────────────────────

    def upsert_pending_pids(self, pids, *, user_id: str | None = None) -> None:
        """Discover PIDs that Step 3 still needs to fetch meta for.

        Each PID becomes an ``artworks`` row with ``meta_updated_at=NULL``
        so :attr:`v_pending_artworks` picks it up. ``INSERT OR IGNORE``
        ensures an already-processed PID (``meta_updated_at`` set) is not
        reverted to pending.

        ``pids`` accepts either a flat iterable of PID strings or an iterable
        of ``(pid, user_id)`` tuples.
        """
        coerced_rows = []  # list of (pid_key, uid_for_this_pid)
        for entry in pids or ():
            if isinstance(entry, tuple):
                raw_pid = entry[0]
                row_uid = entry[1] if len(entry) > 1 else None
            else:
                raw_pid = entry
                row_uid = None
            pid_key = self._coerce_pid(raw_pid)
            if not pid_key:
                continue
            uid = row_uid if row_uid is not None else user_id
            coerced_rows.append((pid_key, None if uid is None else str(uid)))
        if not coerced_rows:
            return
        from collections import defaultdict
        by_uid = defaultdict(list)
        for pid_key, uid in coerced_rows:
            by_uid[uid].append(pid_key)
        for uid, pid_list in by_uid.items():
            self.upsert_artworks(pid_list, user_id=uid)

    def get_pending_pids(self) -> list:
        """Return PIDs that Step 3 still needs to fetch meta for (``v_pending_artworks``)."""
        cur = self._conn().execute("SELECT pid FROM v_pending_artworks")
        return [r[0] for r in cur.fetchall()]

    def mark_pid_done(self, pid: str) -> None:
        """Mark a PID as meta-fetched by setting ``meta_updated_at`` on ``artworks``.

        In normal Step 3 flow this is already done by :meth:`upsert_meta` /
        :meth:`upsert_artwork`. This method is a fallback for cases where the
        PID is considered processed but no real meta is available.
        """
        import datetime
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._conn().execute(
                "UPDATE artworks SET meta_updated_at = ? "
                "WHERE pid = ? AND meta_updated_at IS NULL",
                (stamp, pid_key),
            )

    def pending_pid_count(self) -> int:
        """Count PIDs in ``v_pending_artworks`` (meta not yet fetched)."""
        cur = self._conn().execute("SELECT COUNT(*) FROM v_pending_artworks")
        return int(cur.fetchone()[0])

    # ── artworks (new canonical schema) ───────────────────────────────────

    def upsert_artwork(
        self,
        pid: str,
        *,
        discovered_at: str | None = None,
        page_count: int | None = None,
        like_count: int | None = None,
        tags: list | None = None,
        img_url_template: str | None = None,
        requires_cookie: bool | None = None,
        meta_updated_at: str | None = None,
        revoked_at: str | None = None,
        upload_date: str | None = None,
        create_date: str | None = None,
        user_id: str | None = None,
        user_name: str | None = None,
    ) -> None:
        """Insert or merge an artwork row. ``COALESCE`` keeps existing
        columns whenever the caller passes ``None``, so this method is safe
        to call with partial information (e.g. when first discovering a PID
        before meta is fetched). ``discovered_at`` defaults to *now* on
        first insert; subsequent calls keep the original value.

        ``upload_date`` and ``create_date`` are Pixiv's ISO-8601 timestamps
        (with timezone) for the artwork upload / creation. ``user_id`` /
        ``user_name`` identify the artist; Step 2 knows ``user_id`` from the
        profile-scan iteration, Step 3 supplements ``user_name`` from
        ``/ajax/illust/{id}``. ``meta_updated_at`` doubles as the fetched-at
        timestamp.
        """
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return
        rc_int = None if requires_cookie is None else (1 if requires_cookie else 0)
        tags_blob = None if tags is None else json.dumps(list(tags), ensure_ascii=False)
        self._emit("artwork.upsert", pid=pid_key, discovered_at=discovered_at,
                   page_count=page_count, like_count=like_count,
                   tags=list(tags) if tags is not None else None,
                   img_url_template=img_url_template,
                   requires_cookie=requires_cookie,
                   meta_updated_at=meta_updated_at, revoked_at=revoked_at,
                   upload_date=upload_date, create_date=create_date,
                   user_id=user_id, user_name=user_name)
        sql = (
            "INSERT INTO artworks (pid, discovered_at, page_count, like_count, "
            "tags, img_url_template, requires_cookie, meta_updated_at, revoked_at, "
            "upload_date, create_date, user_id, user_name) "
            "VALUES (?, COALESCE(?, datetime('now')), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(pid) DO UPDATE SET "
            "page_count       = COALESCE(excluded.page_count, artworks.page_count), "
            "like_count       = COALESCE(excluded.like_count, artworks.like_count), "
            "tags             = COALESCE(excluded.tags, artworks.tags), "
            "img_url_template = COALESCE(excluded.img_url_template, artworks.img_url_template), "
            "requires_cookie  = COALESCE(excluded.requires_cookie, artworks.requires_cookie), "
            "meta_updated_at  = COALESCE(excluded.meta_updated_at, artworks.meta_updated_at), "
            "revoked_at       = COALESCE(excluded.revoked_at, artworks.revoked_at), "
            "upload_date      = COALESCE(excluded.upload_date, artworks.upload_date), "
            "create_date      = COALESCE(excluded.create_date, artworks.create_date), "
            "user_id          = COALESCE(excluded.user_id, artworks.user_id), "
            "user_name        = COALESCE(excluded.user_name, artworks.user_name)"
        )
        with self._lock:
            self._conn().execute(sql, (
                pid_key, discovered_at, page_count, like_count, tags_blob,
                img_url_template, rc_int, meta_updated_at, revoked_at,
                upload_date, create_date, user_id, user_name,
            ))

    def upsert_artworks(
        self,
        pids: Iterable,
        *,
        discovered_at: str | None = None,
        user_id: str | None = None,
    ) -> int:
        """Bulk-discover PIDs (no meta yet). Existing rows are untouched.

        Used by migration + Step 2 ingestion. ``discovered_at`` defaults to
        the current wall-clock time when not supplied. Returns the row count
        attempted (``INSERT OR IGNORE`` may skip duplicates).

        ``user_id`` is recorded alongside ``discovered_at`` when supplied —
        Step 2 already knows which artist a PID came from, so writing it at
        discovery time fills in the author field for PIDs that never reach
        Step 3 (e.g. interrupted runs).
        """
        import datetime
        ts = discovered_at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uid = None if user_id is None else str(user_id)
        rows = [(self._coerce_pid(p), ts, uid) for p in (pids or ()) if self._coerce_pid(p)]
        if not rows:
            return 0
        self._emit("artwork.discovered",
                   pids=[r[0] for r in rows],
                   discovered_at=ts,
                   user_id=uid)
        self._bulk_write(
            "INSERT OR IGNORE INTO artworks (pid, discovered_at, user_id) "
            "VALUES (?, ?, ?)",
            rows,
        )
        # Backfill user_id for PIDs that existed before this discovery pass
        # but had no author recorded. INSERT OR IGNORE leaves those rows
        # untouched, so we patch them explicitly. Only NULL→value updates run
        # (never overwrite an existing user_id, even if the caller-passed
        # uid disagrees — keep first writer wins).
        if uid is not None:
            self._bulk_write(
                "UPDATE artworks SET user_id = ? "
                "WHERE pid = ? AND user_id IS NULL",
                [(uid, r[0]) for r in rows],
            )
        return len(rows)

    def get_artwork(self, pid: str) -> dict | None:
        """Read one artwork row as a dict, decoding the JSON tag blob.

        ``meta_updated_at`` is also exposed under the ``fetched_at`` alias so
        callers can use the more user-facing wording without coupling to the
        column name.
        """
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return None
        cur = self._conn().execute(
            "SELECT pid, discovered_at, page_count, like_count, tags, "
            "img_url_template, requires_cookie, meta_updated_at, revoked_at, "
            "upload_date, create_date, user_id, user_name "
            "FROM artworks WHERE pid=?",
            (pid_key,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        try:
            tags = json.loads(row[4]) if row[4] else []
        except Exception:
            tags = []
        return {
            "pid": row[0],
            "discovered_at": row[1],
            "page_count": row[2],
            "like_count": row[3],
            "tags": tags,
            "img_url_template": row[5],
            "requires_cookie": None if row[6] is None else bool(row[6]),
            "meta_updated_at": row[7],
            "fetched_at": row[7],
            "revoked_at": row[8],
            "upload_date": row[9],
            "create_date": row[10],
            "user_id": row[11],
            "user_name": row[12],
        }

    def user_id_map_for_pids(self, pids: Iterable[str]) -> dict[str, str | None]:
        """Return ``{original_pid: user_id|None}`` for the given pids.

        Keys are the exact pid strings passed in (not the coerced digit
        form), so callers can look up by the same values they hold. A pid
        absent from ``artworks``, or whose ``user_id`` is NULL/empty, maps
        to ``None``. Coerced pids are batched in chunks of 900 to stay under
        SQLite's bound-variable limit.
        """
        out: dict[str, str | None] = {}
        coerced_to_orig: dict[str, list[str]] = {}
        for p in pids:
            out[p] = None
            c = self._coerce_pid(p)
            if c:
                coerced_to_orig.setdefault(c, []).append(p)
        if not coerced_to_orig:
            return out
        conn = self._conn()
        keys = list(coerced_to_orig.keys())
        chunk = 900
        for i in range(0, len(keys), chunk):
            part = keys[i:i + chunk]
            placeholders = ",".join("?" * len(part))
            cur = conn.execute(
                f"SELECT pid, user_id FROM artworks WHERE pid IN ({placeholders})",
                part,
            )
            for cpid, uid in cur.fetchall():
                val = uid if (uid is not None and str(uid).strip() != "") else None
                for orig in coerced_to_orig.get(str(cpid), []):
                    out[orig] = val
        return out

    def mark_artwork_revoked(self, pid: str, *, revoked_at: str | None = None) -> None:
        """Mark a PID as removed by Pixiv (404 / deletion). Idempotent."""
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return
        self._emit("artwork.revoked", pid=pid_key, revoked_at=revoked_at)
        ts = revoked_at  # None -> use datetime('now')
        sql = (
            "UPDATE artworks SET revoked_at = COALESCE(?, datetime('now')) "
            "WHERE pid=? AND revoked_at IS NULL"
        )
        with self._lock:
            self._conn().execute(sql, (ts, pid_key))

    def artwork_count(self) -> int:
        return int(self._conn().execute("SELECT COUNT(*) FROM artworks").fetchone()[0])

    def pending_artwork_count(self) -> int:
        """PIDs where Step 3 hasn't yet fetched meta (and not revoked)."""
        return int(self._conn().execute("SELECT COUNT(*) FROM v_pending_artworks").fetchone()[0])

    def get_pending_artwork_pids(self, *, limit: int | None = None) -> list:
        """List PIDs that Step 3 still needs to fetch meta for."""
        sql = "SELECT pid FROM v_pending_artworks"
        if limit is not None:
            sql += " LIMIT ?"
            cur = self._conn().execute(sql, (int(limit),))
        else:
            cur = self._conn().execute(sql)
        return [r[0] for r in cur.fetchall()]

    # ── pages (new canonical schema) ──────────────────────────────────────

    def upsert_page(
        self,
        pid: str,
        page_index: int,
        *,
        status: str,
        url: str | None = None,
        file_path: str | None = None,
        file_size: int | None = None,
        downloaded_at: str | None = None,
        last_attempted_at: str | None = None,
        failure_reason: str | None = None,
        bump_attempt: bool = False,
    ) -> None:
        """Insert or update one page row.

        ``status`` MUST be one of the PAGE_STATUS_* constants — passes through
        a CHECK so callers get a loud error if they fat-finger 'sucess'.
        Other columns use COALESCE so partial updates preserve prior data.
        ``bump_attempt=True`` increments ``attempt_count`` atomically.
        """
        if status not in _VALID_PAGE_STATUSES:
            raise ValueError(f"invalid page status: {status!r}")
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return
        try:
            pidx = int(page_index)
        except (TypeError, ValueError):
            return
        self._emit("page.upsert", pid=pid_key, page_index=pidx, status=status,
                   url=url, file_path=file_path, file_size=file_size,
                   downloaded_at=downloaded_at, last_attempted_at=last_attempted_at,
                   failure_reason=failure_reason, bump_attempt=bump_attempt)
        attempt_delta = 1 if bump_attempt else 0
        sql = (
            "INSERT INTO pages (pid, page_index, status, url, file_path, "
            "file_size, downloaded_at, last_attempted_at, attempt_count, "
            "failure_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(pid, page_index) DO UPDATE SET "
            "status            = excluded.status, "
            "url               = COALESCE(excluded.url, pages.url), "
            "file_path         = COALESCE(excluded.file_path, pages.file_path), "
            "file_size         = COALESCE(excluded.file_size, pages.file_size), "
            "downloaded_at     = COALESCE(excluded.downloaded_at, pages.downloaded_at), "
            "last_attempted_at = COALESCE(excluded.last_attempted_at, pages.last_attempted_at), "
            "attempt_count     = pages.attempt_count + ?, "
            "failure_reason    = COALESCE(excluded.failure_reason, pages.failure_reason)"
        )
        with self._lock:
            self._conn().execute(sql, (
                pid_key, pidx, status, url, file_path, file_size,
                downloaded_at, last_attempted_at, attempt_delta, failure_reason,
                attempt_delta,
            ))

    def mark_page_downloaded(
        self,
        pid: str,
        page_index: int,
        *,
        file_path: str | None = None,
        file_size: int | None = None,
        url: str | None = None,
    ) -> None:
        """Convenience wrapper for the success path. Stamps downloaded_at=now."""
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.upsert_page(
            pid, page_index,
            status=PAGE_STATUS_DOWNLOADED,
            url=url, file_path=file_path, file_size=file_size,
            downloaded_at=ts, last_attempted_at=ts,
        )

    def mark_page_failed(
        self,
        pid: str,
        page_index: int,
        *,
        failure_reason: str,
        url: str | None = None,
    ) -> None:
        """Convenience wrapper for the failure path. Bumps attempt_count."""
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.upsert_page(
            pid, page_index,
            status=PAGE_STATUS_FAILED,
            url=url, last_attempted_at=ts,
            failure_reason=str(failure_reason),
            bump_attempt=True,
        )

    def mark_page_pending(self, pid: str, page_index: int, *, url: str | None = None) -> None:
        """Mark / re-queue a page as pending. Used by the failed-retry path."""
        self.upsert_page(pid, page_index, status=PAGE_STATUS_PENDING, url=url)

    def upsert_pages_bulk(self, rows: Iterable[tuple]) -> int:
        """Bulk INSERT OR IGNORE pages. Each row is
        ``(pid, page_index, status, url, file_path)`` — ``url`` and
        ``file_path`` may be ``None``. Used by the migration script to seed
        the table from disk scan + pending_urls in one transaction.

        Returns the number of (pid, page_index) tuples attempted.
        """
        clean: list = []
        for row in rows or ():
            try:
                pid_raw, pidx_raw, status, url, file_path = row
            except (TypeError, ValueError):
                continue
            if status not in _VALID_PAGE_STATUSES:
                continue
            pid_key = self._coerce_pid(pid_raw)
            if not pid_key:
                continue
            try:
                pidx = int(pidx_raw)
            except (TypeError, ValueError):
                continue
            clean.append((pid_key, pidx, status, url, file_path))
        if not clean:
            return 0
        self._emit("pages.upsert_bulk", rows=[list(r) for r in clean])
        self._bulk_write(
            "INSERT OR IGNORE INTO pages "
            "(pid, page_index, status, url, file_path, attempt_count) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            clean,
        )
        return len(clean)

    def get_page(self, pid: str, page_index: int) -> dict | None:
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return None
        cur = self._conn().execute(
            "SELECT status, url, file_path, file_size, downloaded_at, "
            "last_attempted_at, attempt_count, failure_reason "
            "FROM pages WHERE pid=? AND page_index=?",
            (pid_key, int(page_index)),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "status": row[0],
            "url": row[1],
            "file_path": row[2],
            "file_size": row[3],
            "downloaded_at": row[4],
            "last_attempted_at": row[5],
            "attempt_count": row[6],
            "failure_reason": row[7],
        }

    def get_pages_for_pid(self, pid: str) -> list:
        """Return [(page_index, status, file_path), ...] sorted by page_index."""
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return []
        cur = self._conn().execute(
            "SELECT page_index, status, file_path FROM pages "
            "WHERE pid=? ORDER BY page_index", (pid_key,),
        )
        return cur.fetchall()

    def get_pending_pages(self, *, limit: int | None = None) -> list:
        """Return [(pid, page_index, url), ...] for pages awaiting download."""
        sql = "SELECT pid, page_index, url FROM v_pending_pages"
        if limit is not None:
            sql += " LIMIT ?"
            cur = self._conn().execute(sql, (int(limit),))
        else:
            cur = self._conn().execute(sql)
        return cur.fetchall()

    def pids_with_pending_pages(self) -> list[str]:
        """Distinct PIDs that still have at least one pending page.

        Reads the canonical ``v_pending_pages`` view. Used by combined
        (邊查邊下) mode to absorb PIDs that a partial Step 3 already
        resolved (meta written, pages pending) but never downloaded.
        """
        try:
            cur = self._conn().execute("SELECT DISTINCT pid FROM v_pending_pages")
            return [str(row[0]) for row in cur.fetchall()]
        except Exception:
            return []

    def get_retriable_failed_pages(
        self, *, max_attempts: int = 5, cooldown_hours: int = 24,
    ) -> list:
        """Return [(url, pid), ...] for failed pages eligible for auto-retry.

        A row qualifies when: status='failed', attempt_count < max_attempts,
        a non-NULL url is present, and either last_attempted_at is NULL or
        the cooldown has elapsed. The cooldown is parameter-bound via the
        modifier form ``datetime('now', ?)`` so callers can't smuggle SQL in.
        """
        sql = (
            "SELECT url, pid FROM pages "
            "WHERE status='failed' "
            "  AND attempt_count < ? "
            "  AND url IS NOT NULL "
            "  AND (last_attempted_at IS NULL "
            "       OR datetime(last_attempted_at) < datetime('now', ?))"
        )
        cooldown_modifier = f"-{int(cooldown_hours)} hours"
        cur = self._conn().execute(sql, (int(max_attempts), cooldown_modifier))
        return cur.fetchall()

    def is_pid_complete(self, pid: str) -> bool:
        """True iff this PID has downloaded all known pages (per artworks.page_count)."""
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return False
        cur = self._conn().execute(
            "SELECT 1 FROM v_complete_artworks WHERE pid=? LIMIT 1", (pid_key,),
        )
        return cur.fetchone() is not None

    def complete_artwork_set(self) -> set[str]:
        """Snapshot of all PIDs whose pages are fully downloaded."""
        cur = self._conn().execute("SELECT pid FROM v_complete_artworks")
        return {str(r[0]) for r in cur.fetchall()}

    def closed_artwork_set(self) -> set[str]:
        """All PIDs Step 3 / Step 4 should skip — complete + revoked + legacy.

        Replaces the old ``exist_pid.json`` set at the read boundary.
        This is the set the workers' Python-side filters should consult.
        """
        cur = self._conn().execute("SELECT pid FROM v_closed_artworks")
        return {str(r[0]) for r in cur.fetchall()}

    def is_pid_closed(self, pid: str) -> bool:
        """Single-PID variant of :meth:`closed_artwork_set` — cheap point query."""
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return False
        cur = self._conn().execute(
            "SELECT 1 FROM v_closed_artworks WHERE pid=? LIMIT 1", (pid_key,)
        )
        return cur.fetchone() is not None

    def page_status_counts(self) -> dict:
        """Aggregate ``pages`` rows by status — useful for UI / diagnostics."""
        cur = self._conn().execute(
            "SELECT status, COUNT(*) FROM pages GROUP BY status"
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _coerce_pid(value) -> str:
        """Normalize a PID input to its bare digit string.

        Accepts ints, plain digit strings, and ``"<pid>_p<n>"`` page suffixes.
        Empty / non-numeric values yield ``""`` so the caller can short-circuit.
        """
        s = str(value or "").strip()
        if not s:
            return ""
        if "_" in s:
            s = s.split("_", 1)[0]
        # Keep only leading digits — guards against URL fragments slipping in.
        digits = []
        for ch in s:
            if ch.isdigit():
                digits.append(ch)
            else:
                break
        return "".join(digits)


def open_metadata_db(path, json_meta=None, *, event_log=None):
    """Open MetadataDB at ``path`` and migrate ``json_meta`` once on first run.

    Returns the opened DB, or ``None`` on any failure (caller treats DB as
    optional — every callsite guards with ``getattr(self, "_metadata_db", None)``).
    """
    try:
        db = MetadataDB(path, event_log=event_log)
        if isinstance(json_meta, dict) and json_meta and db.meta_count() == 0:
            db.import_meta_dict(json_meta)
        return db
    except Exception:
        return None


def emit_db_stats(db, q, stage="Step"):
    """Push a one-line summary of meta/downloaded row counts to ``q``.

    No-op when ``db`` is None or any DB call / queue put fails — purely
    informational for the UI log panel.
    """
    if db is None:
        return
    try:
        meta_n = db.meta_count()
        dl_n = db.downloaded_count()
    except Exception:
        return
    try:
        from app.core.worker_event import WorkerEvent
        q.put(WorkerEvent(
            "output",
            f"<p><font color='gray'>[{stage} SQLite] metadata.sqlite3 載入 "
            f"{meta_n} 筆 meta、{dl_n} 筆已下載</font></p>",
        ))
    except Exception:
        pass


def mirror_exist_pid_set(db, exist_pid):
    """Bulk-import a downloaded-PID set into the SQLite cache.

    No-op when ``db`` is None or ``exist_pid`` is empty.  Silently swallows
    exceptions (best-effort sync)."""
    if db is None or not exist_pid:
        return
    with contextlib.suppress(Exception):
        db.import_downloaded_set(exist_pid)
