"""Closed / complete artwork-set queries for :class:`MetadataDB`.

Extracted from ``metadata_db.py`` (file-size refactor). The closed set —
complete + revoked + legacy-sentinel PIDs — is the single hottest call in
the startup path; this module holds its Python-side composition, the
process-cache wrapper, and the cheap per-PID membership variants.

Mixed into ``MetadataDB`` via ``_ClosedSetMixin``; the methods run against
the connection, lock, PID coercion and the closed-set process-cache
primitives (``_conn`` / ``_coerce_pid`` and ``_db_file_signature`` /
``_CLOSED_SET_CACHE`` / ``_CLOSED_SET_CACHE_LOCK`` from
``metadata_db_cache``) provided by / shared with the concrete class.
"""
from __future__ import annotations

import os

from app.core.metadata_db_cache import (
    _CLOSED_SET_CACHE,
    _CLOSED_SET_CACHE_LOCK,
    _db_file_signature,
)


class _ClosedSetMixin:
    """Closed / complete artwork-set queries, mixed into ``MetadataDB``."""

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

    def _compute_closed_artwork_set(self) -> set[str]:
        """Compose the closed set in Python instead of via the
        ``v_closed_artworks`` SQL UNION.

        The view's 3-branch ``UNION`` forces SQLite to spool ~1.1M rows into
        a TEMP B-TREE just to dedupe — ~23s on the real DB. Composing the
        same three branches as Python sets (set ops dedupe for free) is
        ~3-7s and yields a byte-identical result:

          branch 1  revoked_at IS NOT NULL                    -> revoked
          branch 2  v_complete_artworks                        -> complete
          branch 3  sentinel rows with no pending page         -> sentinels - pending

        ``closed = (sentinels - pending) | complete | revoked``.
        """
        conn = self._conn()

        def _col(sql: str) -> set[str]:
            return {str(r[0]) for r in conn.execute(sql).fetchall()}

        revoked = _col("SELECT pid FROM artworks WHERE revoked_at IS NOT NULL")
        complete = _col(
            "SELECT a.pid FROM artworks a JOIN ("
            "SELECT pid, COUNT(*) AS done FROM pages "
            "WHERE status='downloaded' GROUP BY pid"
            ") c ON c.pid = a.pid "
            "WHERE a.page_count IS NOT NULL AND c.done >= a.page_count"
        )
        sentinels = _col(
            "SELECT pid FROM artworks WHERE meta_updated_at = '0001-01-01 00:00:00'"
        )
        pending = _col("SELECT pid FROM pages WHERE status = 'pending'")
        return (sentinels - pending) | complete | revoked

    def closed_artwork_set(self) -> set[str]:
        """All PIDs Step 3 / Step 4 should skip — complete + revoked + legacy.

        Replaces the old ``exist_pid.json`` set at the read boundary.
        This is the set the workers' Python-side filters should consult.

        Result is process-cached by DB file signature (see ``_db_file_signature``):
        repeated calls within a run, or across runs while the DB is unchanged,
        return instantly instead of re-scanning ~1.1M rows. A copy is returned
        so callers may mutate it freely without corrupting the cache.
        """
        key = os.path.normcase(os.path.normpath(self._path))
        sig = _db_file_signature(self._path)
        with _CLOSED_SET_CACHE_LOCK:
            cached = _CLOSED_SET_CACHE.get(key)
            if cached is not None and cached[0] == sig:
                return set(cached[1])
        result = self._compute_closed_artwork_set()
        with _CLOSED_SET_CACHE_LOCK:
            _CLOSED_SET_CACHE[key] = (sig, set(result))
        return result

    def is_pid_closed(self, pid: str) -> bool:
        """Single-PID variant of :meth:`closed_artwork_set` — cheap point query."""
        pid_key = self._coerce_pid(pid)
        if not pid_key:
            return False
        cur = self._conn().execute(
            "SELECT 1 FROM v_closed_artworks WHERE pid=? LIMIT 1", (pid_key,)
        )
        return cur.fetchone() is not None
