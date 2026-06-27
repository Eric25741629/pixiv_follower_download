"""``artworks``-table CRUD for :class:`MetadataDB`.

Extracted from ``metadata_db.py`` (file-size refactor). One row per Pixiv
ID the app has seen — discovery, meta upsert, author backfill, revocation
and the pending/complete counts that drive Step 3's work queue.

Mixed into ``MetadataDB`` via ``_ArtworkMixin``; the methods run against the
connection, lock, bulk-writer, PID coercion and event emitter
(``_conn`` / ``_lock`` / ``_bulk_write`` / ``_coerce_pid`` / ``_emit``)
provided by the concrete class.
"""
from __future__ import annotations

import json
from collections.abc import Iterable


class _ArtworkMixin:
    """``artworks``-table methods, mixed into ``MetadataDB``."""

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

    def backfill_user_ids(self, pids: Iterable, user_id: str | None) -> int:
        """Fill ``user_id`` for EXISTING artworks rows that have no author yet.

        UPDATE-only: never inserts a row, so it cannot create spurious
        ``v_pending_artworks`` entries or otherwise disturb any work queue.
        Callers can therefore pass an artist's full PID list (including PIDs
        the Step 2 incremental scan truncated) to fill the author for
        already-known PIDs without re-querying them. First-writer-wins: a row
        that already has a non-empty ``user_id`` is left untouched. Returns
        the number of PIDs considered (not the number actually changed).
        """
        uid = None if user_id is None else str(user_id).strip()
        if not uid:
            return 0
        rows = [self._coerce_pid(p) for p in (pids or ())]
        rows = [r for r in rows if r]
        if not rows:
            return 0
        self._emit("artwork.user_id_backfill", pids=rows, user_id=uid)
        self._bulk_write(
            "UPDATE artworks SET user_id = ? "
            "WHERE pid = ? AND (user_id IS NULL OR user_id = '')",
            [(uid, r) for r in rows],
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
