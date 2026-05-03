"""SQLite-backed metadata cache.

Replaces / supplements the JSON files used by Steps 3 and 4:

  * ``all_url_meta.json``  — per-PID metadata (tag list, like count, page count,
    img URL, requires_cookie flag).
  * ``exist_pid.json``     — flat set of PIDs that have already been
    downloaded.

The store is a single ``metadata.sqlite3`` file in the same APPDATA folder
the JSON files live in. Two tables:

    pids            (pid TEXT PK, tags JSON, like_count INT, page_count INT,
                     img_url TEXT, requires_cookie INT, updated_at TEXT)
    downloaded      (pid TEXT PK, downloaded_at TEXT)

Concurrency: SQLite is opened in WAL mode, which lets the four worker
threads (Steps 2/3/4) share a connection-per-thread without contention.
``MetadataDB`` is intentionally tiny — call sites can drop straight in
without learning a new ORM. JSON import/export helpers are provided so
the existing JSON files stay authoritative until the SQLite cache is
fully wired in (next iteration).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Iterable

DB_FILENAME = "metadata.sqlite3"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS pids (
    pid TEXT PRIMARY KEY,
    tags TEXT,
    like_count INTEGER,
    page_count INTEGER,
    img_url TEXT,
    requires_cookie INTEGER,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS downloaded (
    pid TEXT PRIMARY KEY,
    downloaded_at TEXT
);
CREATE TABLE IF NOT EXISTS pending_urls (
    url     TEXT PRIMARY KEY,
    pid     TEXT,
    status  TEXT NOT NULL DEFAULT 'pending',
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS pending_pids (
    pid     TEXT PRIMARY KEY,
    status  TEXT NOT NULL DEFAULT 'pending',
    added_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pids_requires_cookie
    ON pids(requires_cookie);
CREATE INDEX IF NOT EXISTS idx_pending_urls_status ON pending_urls(status);
CREATE INDEX IF NOT EXISTS idx_pending_pids_status ON pending_pids(status);
"""


class MetadataDB:
    """Thread-safe SQLite cache for Pixiv metadata + downloaded-PID set."""

    def __init__(self, base_path: str):
        self._base = str(base_path or "").strip()
        self._path = os.path.join(self._base, DB_FILENAME)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._initialized = False

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
                self._initialized = True
        self._local.conn = conn  # cached only after successful init
        return conn

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
                src.execute("PRAGMA wal_checkpoint(PASSIVE)")
                dst = sqlite3.connect(dst_path, timeout=10.0, isolation_level=None)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            # Prune old backups — keep the most recent max_history copies
            files = sorted(
                [f for f in os.listdir(hist_dir) if f.startswith(base + ".")],
                key=lambda f: os.path.getmtime(os.path.join(hist_dir, f)),
                reverse=True,
            )
            for old in files[max_history:]:
                try:
                    os.remove(os.path.join(hist_dir, old))
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def _bulk_write(self, sql: str, rows: list) -> None:
        """Execute a bulk write in a single explicit transaction.

        isolation_level=None means autocommit — without this wrapper every
        row in executemany() would be its own BEGIN/COMMIT, making bulk
        inserts of thousands of rows unnecessarily slow.
        """
        if not rows:
            return
        with self._lock:
            conn = self._conn()
            conn.execute("BEGIN")
            try:
                conn.executemany(sql, rows)
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
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
        rc_int = None if requires_cookie is None else (1 if requires_cookie else 0)
        tags_blob = None if tags is None else json.dumps(list(tags), ensure_ascii=False)
        sql = (
            "INSERT INTO pids (pid, tags, like_count, page_count, img_url, "
            "requires_cookie, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(pid) DO UPDATE SET "
            "tags = COALESCE(excluded.tags, pids.tags), "
            "like_count = COALESCE(excluded.like_count, pids.like_count), "
            "page_count = COALESCE(excluded.page_count, pids.page_count), "
            "img_url = COALESCE(excluded.img_url, pids.img_url), "
            "requires_cookie = COALESCE(excluded.requires_cookie, pids.requires_cookie), "
            "updated_at = COALESCE(excluded.updated_at, pids.updated_at)"
        )
        with self._lock:
            self._conn().execute(
                sql,
                (pid_key, tags_blob, like_count, page_count, img_url, rc_int, updated_at),
            )

    def get_meta(self, pid: str) -> dict | None:
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return None
        cur = self._conn().execute(
            "SELECT tags, like_count, page_count, img_url, requires_cookie, updated_at "
            "FROM pids WHERE pid = ?",
            (pid_key,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        tags_blob, like, pages, img, rc, updated = row
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
        }

    def has_meta(self, pid: str) -> bool:
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return False
        cur = self._conn().execute(
            "SELECT 1 FROM pids WHERE pid = ? LIMIT 1", (pid_key,)
        )
        return cur.fetchone() is not None

    def meta_count(self) -> int:
        cur = self._conn().execute("SELECT COUNT(*) FROM pids")
        return int(cur.fetchone()[0])

    # ── downloaded-pid set ────────────────────────────────────────────────

    def mark_downloaded(self, pid: str, *, downloaded_at: str | None = None) -> None:
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return
        with self._lock:
            self._conn().execute(
                "INSERT OR REPLACE INTO downloaded (pid, downloaded_at) VALUES (?, ?)",
                (pid_key, downloaded_at),
            )

    def is_downloaded(self, pid: str) -> bool:
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return False
        cur = self._conn().execute(
            "SELECT 1 FROM downloaded WHERE pid = ? LIMIT 1", (pid_key,)
        )
        return cur.fetchone() is not None

    def downloaded_set(self) -> set[str]:
        cur = self._conn().execute("SELECT pid FROM downloaded")
        return {str(r[0]) for r in cur.fetchall()}

    def downloaded_count(self) -> int:
        cur = self._conn().execute("SELECT COUNT(*) FROM downloaded")
        return int(cur.fetchone()[0])

    # ── bulk migration helpers ────────────────────────────────────────────

    @staticmethod
    def _build_meta_row(pid_key, entry):
        """Translate one (pid, entry) into a row tuple for the pids table."""
        tags = entry.get("tag")
        if tags is None:
            tags = entry.get("tags", [])
        tags_blob = json.dumps(
            list(tags) if isinstance(tags, list) else [],
            ensure_ascii=False,
        )
        rc = entry.get("requires_cookie")
        rc_int = None if rc is None else (1 if rc else 0)
        return (
            pid_key,
            tags_blob,
            entry.get("like"),
            entry.get("pagecount") or entry.get("page_count"),
            entry.get("img_url"),
            rc_int,
            entry.get("updated_at") or entry.get("checked_at"),
        )

    def import_meta_dict(self, meta: dict) -> int:
        """Bulk-insert from the existing all_url_meta.json shape.

        Returns the number of rows inserted/updated.
        """
        if not isinstance(meta, dict) or not meta:
            return 0
        rows = []
        for pid, entry in meta.items():
            pid_key = self._coerce_pid(pid)
            if not pid_key or not isinstance(entry, dict):
                continue
            rows.append(self._build_meta_row(pid_key, entry))
        if not rows:
            return 0
        self._bulk_write(
            "INSERT OR REPLACE INTO pids "
            "(pid, tags, like_count, page_count, img_url, requires_cookie, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return len(rows)

    def import_downloaded_set(self, pids: Iterable) -> int:
        rows = []
        for v in pids or ():
            pid_key = self._coerce_pid(v)
            if pid_key:
                rows.append((pid_key, None))
        if not rows:
            return 0
        self._bulk_write(
            "INSERT OR REPLACE INTO downloaded (pid, downloaded_at) VALUES (?, ?)",
            rows,
        )
        return len(rows)

    def export_meta_dict(self) -> dict:
        """Return the canonical dict shape, suitable for writing all_url_meta.json."""
        cur = self._conn().execute(
            "SELECT pid, tags, like_count, page_count, img_url, requires_cookie, "
            "updated_at FROM pids"
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
        """Bulk INSERT OR IGNORE (url, pid) pairs; never overwrites existing status."""
        if not entries:
            return
        rows = [(str(u), str(p)) for u, p in entries if u]
        if not rows:
            return
        self._bulk_write(
            "INSERT OR IGNORE INTO pending_urls (url, pid, added_at)"
            " VALUES (?, ?, datetime('now'))",
            rows,
        )

    def get_pending_urls(self) -> list:
        """Return [(url, pid)] for all rows with status='pending'."""
        cur = self._conn().execute(
            "SELECT url, pid FROM pending_urls WHERE status = 'pending'"
        )
        return cur.fetchall()

    def mark_url_done(self, url: str) -> None:
        """Delete a URL row — pending_urls only stores truly pending entries."""
        with self._lock:
            self._conn().execute(
                "DELETE FROM pending_urls WHERE url=?", (str(url),)
            )

    def mark_urls_done(self, urls) -> None:
        """Bulk-delete downloaded URL rows to prevent table bloat."""
        rows = [(str(u),) for u in urls if u]
        if not rows:
            return
        self._bulk_write("DELETE FROM pending_urls WHERE url=?", rows)

    def pending_url_count(self) -> int:
        cur = self._conn().execute(
            "SELECT COUNT(*) FROM pending_urls WHERE status='pending'"
        )
        return int(cur.fetchone()[0])

    def get_pending_urls_filtered(self, like_min: int = 0) -> list:
        """Return [(url, pid)] for pending URLs, pre-filtered in SQL.

        Two filters are applied at the database level to reduce the result set
        before Python sees it:

        1. Exclude PIDs already in the ``downloaded`` table (exist_pid check).
        2. When *like_min* > 0, exclude PIDs whose ``like_count`` is known and
           below the threshold.  PIDs with no metadata (like_count IS NULL) are
           kept so Step 4 can attempt a network fetch for them.

        Tags filtering and special_like_rules cannot be expressed efficiently in
        SQL and remain in Python (_prepare_download_tasks).
        """
        if like_min > 0:
            sql = """
                SELECT pu.url, pu.pid
                FROM pending_urls pu
                LEFT JOIN pids p ON pu.pid = p.pid
                WHERE pu.status = 'pending'
                  AND NOT EXISTS (
                      SELECT 1 FROM downloaded d WHERE d.pid = pu.pid
                  )
                  AND (p.like_count IS NULL OR p.like_count >= ?)
            """
            cur = self._conn().execute(sql, (like_min,))
        else:
            sql = """
                SELECT pu.url, pu.pid
                FROM pending_urls pu
                WHERE pu.status = 'pending'
                  AND NOT EXISTS (
                      SELECT 1 FROM downloaded d WHERE d.pid = pu.pid
                  )
            """
            cur = self._conn().execute(sql)
        return cur.fetchall()

    def url_row_count(self) -> int:
        """Total rows in pending_urls regardless of status."""
        cur = self._conn().execute("SELECT COUNT(*) FROM pending_urls")
        return int(cur.fetchone()[0])

    def import_pending_urls_from_file(self, path: str) -> int:
        """First-run migration: read all_url.txt and INSERT OR IGNORE into pending_urls."""
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
        except OSError:
            return 0
        entries = []
        for url in lines:
            try:
                filename = url.rsplit("/", 1)[-1]
                pid = filename.split("_", 1)[0]
                if not pid.isdigit():
                    pid = ""
            except Exception:
                pid = ""
            entries.append((url, pid))
        self.upsert_pending_urls(entries)
        return len(entries)

    # ── pending_pids ──────────────────────────────────────────────────────

    def upsert_pending_pids(self, pids) -> None:
        """Bulk INSERT OR IGNORE; never overwrites existing status."""
        rows = [(self._coerce_pid(p),) for p in pids if self._coerce_pid(p)]
        if not rows:
            return
        self._bulk_write(
            "INSERT OR IGNORE INTO pending_pids (pid, added_at)"
            " VALUES (?, datetime('now'))",
            rows,
        )

    def get_pending_pids(self) -> list:
        """Return list of pid strings with status='pending'."""
        cur = self._conn().execute(
            "SELECT pid FROM pending_pids WHERE status='pending'"
        )
        return [r[0] for r in cur.fetchall()]

    def mark_pid_done(self, pid: str) -> None:
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return
        with self._lock:
            self._conn().execute(
                "UPDATE pending_pids SET status='done' WHERE pid=?", (pid_key,)
            )

    def pending_pid_count(self) -> int:
        cur = self._conn().execute(
            "SELECT COUNT(*) FROM pending_pids WHERE status='pending'"
        )
        return int(cur.fetchone()[0])

    def import_pending_pids_from_file(self, path: str) -> int:
        """First-run migration: read pictures_id.txt and INSERT OR IGNORE."""
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                pids = [ln.strip() for ln in f if ln.strip()]
        except OSError:
            return 0
        self.upsert_pending_pids(pids)
        return len(pids)

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
