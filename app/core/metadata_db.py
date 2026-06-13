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
import contextlib

# Pieces split into sibling modules (file-size refactor), imported here so
# the public API and existing ``from app.core.metadata_db import ...`` callers
# are unchanged. ``_SCHEMA`` feeds ``_conn``'s executescript; the cache
# primitives back ``closed_artwork_set`` (and the test suite reads
# ``mdb._db_file_signature``); the mixins carry relocated method groups.
from app.core.metadata_db_schema import _SCHEMA  # noqa: F401  (internal re-export)
from app.core.metadata_db_cache import (  # noqa: F401  (public re-export)
    _CLOSED_SET_CACHE,
    _CLOSED_SET_CACHE_LOCK,
    _db_file_signature,
)
from app.core.metadata_db_migration import _MigrationMixin
from app.core.metadata_db_artwork import _ArtworkMixin
# pages-table CRUD + pending-URL helpers and the closed-set queries moved to
# sibling modules (file-size refactor); the PAGE_STATUS_* constants are owned
# by metadata_db_pages and re-imported here so existing
# ``from app.core.metadata_db import PAGE_STATUS_PENDING`` callers (and the
# star surface) are unchanged.
from app.core.metadata_db_pages import (  # noqa: F401  (public re-export)
    PAGE_STATUS_DOWNLOADED,
    PAGE_STATUS_FAILED,
    PAGE_STATUS_PENDING,
    PAGE_STATUS_REVOKED,
    _VALID_PAGE_STATUSES,
    _PagesMixin,
)
from app.core.metadata_db_closed_set import _ClosedSetMixin

DB_FILENAME = "metadata.sqlite3"


class MetadataDB(_MigrationMixin, _ArtworkMixin, _PagesMixin, _ClosedSetMixin):
    """Thread-safe SQLite cache for Pixiv metadata + downloaded-PID set.

    Method groups are split across mixins (file-size refactor) but the
    public API is unchanged — every method is still reachable as
    ``db.<method>``. ``_MigrationMixin`` holds the legacy JSON import/export
    helpers (``metadata_db_migration.py``); ``_ArtworkMixin`` holds the
    ``artworks``-table CRUD (``metadata_db_artwork.py``); ``_PagesMixin``
    holds the ``pages``-table CRUD + pending-URL helpers
    (``metadata_db_pages.py``); ``_ClosedSetMixin`` holds the closed /
    complete artwork-set queries (``metadata_db_closed_set.py``). The
    connection, lock, bulk-writer and shared helpers they call live on this
    class.
    """

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
            "FROM artworks WHERE pid = ? AND meta_updated_at IS NOT NULL "
            "AND meta_updated_at != '0001-01-01 00:00:00'",
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
            "AND meta_updated_at IS NOT NULL "
            "AND meta_updated_at != '0001-01-01 00:00:00' LIMIT 1", (pid_key,)
        )
        return cur.fetchone() is not None

    def meta_count(self) -> int:
        """Count artworks for which meta has ever been recorded."""
        cur = self._conn().execute(
            "SELECT COUNT(*) FROM artworks WHERE meta_updated_at IS NOT NULL "
            "AND meta_updated_at != '0001-01-01 00:00:00'"
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
        """Count all closed PIDs. Routes through the cached
        :meth:`closed_artwork_set` so ``emit_db_stats`` no longer pays the
        ~23s ``SELECT COUNT(*) FROM v_closed_artworks`` scan on every step."""
        return len(self.closed_artwork_set())

    # ── bulk migration helpers ────────────────────────────────────────────

    # import_meta_dict / _build_artwork_row / import_downloaded_set /
    # export_meta_dict moved to metadata_db_migration._MigrationMixin
    # (file-size refactor). MetadataDB inherits them unchanged.

    # ── pending_urls ──────────────────────────────────────────────────────

    # upsert_pending_urls / get_pending_urls / mark_url_done /
    # mark_pages_downloaded_bulk / mark_urls_done / pending_url_count /
    # get_pending_urls_filtered / url_row_count moved to
    # metadata_db_pages._PagesMixin (file-size refactor). MetadataDB inherits
    # them unchanged.

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

    # upsert_artwork / upsert_artworks / backfill_user_ids / get_artwork /
    # user_id_map_for_pids / mark_artwork_revoked / artwork_count /
    # pending_artwork_count / get_pending_artwork_pids moved to
    # metadata_db_artwork._ArtworkMixin (file-size refactor). MetadataDB
    # inherits them unchanged.

    # ── pages (new canonical schema) ──────────────────────────────────────

    # upsert_page / mark_page_downloaded / mark_page_failed / mark_page_pending /
    # upsert_pages_bulk / get_page / get_pages_for_pid / get_pending_pages /
    # pids_with_pending_pages / get_retriable_failed_pages / page_status_counts
    # moved to metadata_db_pages._PagesMixin (file-size refactor). The
    # closed/complete artwork-set queries (is_pid_complete /
    # complete_artwork_set / _compute_closed_artwork_set / closed_artwork_set /
    # is_pid_closed) moved to metadata_db_closed_set._ClosedSetMixin.
    # MetadataDB inherits them all unchanged.

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
