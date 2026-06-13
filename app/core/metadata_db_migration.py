"""Legacy JSON ⇄ DB migration helpers for :class:`MetadataDB`.

Extracted from ``metadata_db.py`` (file-size refactor). These are the
PHASE-A→B transitional importers/exporters that bridge the old
``all_url_meta.json`` / ``exist_pid.json`` files and the canonical
``artworks`` table. Isolating them flags the whole group as eventually
removable once the JSON fallbacks are dropped (grep ``PHASE-A`` /
``PHASE-B``).

Mixed into ``MetadataDB`` via ``_MigrationMixin``; the methods run against
the connection, bulk-writer, PID coercion and event emitter
(``_conn`` / ``_bulk_write`` / ``_coerce_pid`` / ``_emit``) provided by the
concrete class.
"""
from __future__ import annotations

import json
from collections.abc import Iterable


class _MigrationMixin:
    """Bulk JSON-import / export methods, mixed into ``MetadataDB``."""

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
