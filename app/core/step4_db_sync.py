"""SQLite metadata-DB sync helpers for ``download_thread`` (file-size refactor).

Opening the metadata cache, mirroring the in-memory ``exist_pid`` / ``url_meta``
into SQLite, translating a url_meta entry to upsert kwargs, marking completed
URLs / shadowing failures into the ``pages`` table, and the periodic/closed-set
flush helpers. Mixed into ``download_thread`` via ``_Step4DbSyncMixin``; every
method reaches worker state (``self._metadata_db`` / ``self.url_meta`` /
``self.exist_pid`` / ``self.allurl`` / ``self._completed_urls`` / the locks)
through inheritance, so behaviour is unchanged. These are thin best-effort
wrappers — the DB is authoritative and each call is a no-op when it is
unavailable.
"""
from __future__ import annotations

import contextlib

from app.core.metadata_db import emit_db_stats, mirror_exist_pid_set, open_metadata_db
from app.core.pixiv_thread_utils import mirror_meta_dict_to_db, normalize_pid


class _Step4DbSyncMixin:
    def _init_metadata_db(self, json_meta):
        """Open the SQLite metadata cache and migrate JSON contents on first use."""
        base = getattr(self, "_db_base", None) or self.path
        return open_metadata_db(base, json_meta, event_log=getattr(self, "_event_log", None))

    def _emit_metadata_db_stats(self, stage="Step"):
        """Print a one-liner with current SQLite cache size."""
        emit_db_stats(getattr(self, "_metadata_db", None), self._q, stage=stage)

    def _mirror_exist_pid_to_db(self):
        """Best-effort copy of the in-memory exist_pid set into the SQLite cache."""
        mirror_exist_pid_set(getattr(self, "_metadata_db", None), self.exist_pid)

    def _sync_meta_to_db(self):
        """Mirror the in-memory ``self.url_meta`` into the SQLite cache."""
        mirror_meta_dict_to_db(getattr(self, "_metadata_db", None), self.url_meta)

    @staticmethod
    def _meta_to_db_kwargs(meta):
        """Translate a self.url_meta entry to MetadataDB.upsert_meta kwargs.

        Tolerates non-dict input (returns all-None fields) and accepts both
        ``tag``/``tags`` for tag list aliases plus ``pagecount``/``page_count``
        and ``updated_at``/``checked_at`` for timestamp aliases.
        """
        if not isinstance(meta, dict):
            return {
                "tags": None, "like_count": None, "page_count": None,
                "img_url": None, "requires_cookie": None, "updated_at": None,
            }
        tags = meta.get("tag")
        if tags is None:
            tags = meta.get("tags")
        return {
            "tags": list(tags) if isinstance(tags, list) else None,
            "like_count": meta.get("like"),
            "page_count": meta.get("pagecount") or meta.get("page_count"),
            "img_url": meta.get("img_url"),
            "requires_cookie": meta.get("requires_cookie"),
            "updated_at": meta.get("updated_at") or meta.get("checked_at"),
        }

    def _upsert_meta_in_db(self, pid_key, meta):
        """Best-effort per-PID upsert into SQLite (no-op if DB is unavailable)."""
        db = getattr(self, "_metadata_db", None)
        if db is None or not pid_key:
            return
        with contextlib.suppress(Exception):
            db.upsert_meta(pid_key, **self._meta_to_db_kwargs(meta))

    def _persist_url_meta(self):
        """Sync self.url_meta to SQLite (DB is now the primary store)."""
        lock = getattr(self, "_url_meta_lock", None)
        if lock is not None:
            lock.acquire()
        try:
            self._sync_meta_to_db()
        finally:
            if lock is not None:
                lock.release()

    def _mark_completed_urls_in_db(self):
        """Mark URLs whose page is confirmed on disk as 'downloaded' in SQLite.

        Only genuinely-completed URLs (see :meth:`_record_completed`) are
        marked — a network-retry-exhausted or stop-interrupted URL is *not* in
        ``self._completed_urls`` and therefore stays ``status='pending'`` so the
        next run re-queues it. Silently skips if the DB is not wired or anything
        goes wrong — this is purely an optimisation for subsequent runs."""
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return
        try:
            with self._completed_urls_lock:
                completed = set(self._completed_urls)
            done_urls = [u for u in self.allurl if u in completed]
            db.mark_urls_done(done_urls)
        except Exception:
            pass

    def _shadow_mark_failures(self, fail_records) -> None:
        """Mirror err_url.txt writes into ``pages(status='failed')``.

        ``fail_records`` is the list of ``[url, info]`` produced by
        ``_classify_download_results``; we parse each URL into (pid, page)
        and call ``mark_page_failed`` so Phase 6's auto-retry path can
        pick them up.  Silent on any failure — shadow write is best-effort.
        """
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return
        try:
            from app.core.pid_filesystem import parse_pid_and_page_from_url
            for url_text, info_text in fail_records:
                pid, pidx = parse_pid_and_page_from_url(str(url_text))
                if pid is None or pidx is None:
                    continue
                db.mark_page_failed(
                    pid, pidx,
                    failure_reason=str(info_text),
                    url=str(url_text),
                )
        except Exception:
            pass

    def _maybe_flush_exist_pid(self, pid: str) -> None:
        """Add pid to exist_pid and mark it closed in DB immediately.

        Uses :meth:`~MetadataDB.import_downloaded_set` which writes a
        sentinel ``artworks`` row — visible to ``v_closed_artworks`` and
        therefore to :meth:`~MetadataDB.is_downloaded`.
        """
        pid_key = normalize_pid(pid) or str(pid)
        if pid_key:
            self.exist_pid.add(pid_key)
            db = getattr(self, "_metadata_db", None)
            if db is not None:
                with contextlib.suppress(Exception):
                    db.import_downloaded_set([pid_key])

    # 每 N 個 PID 組之後額外 flush url_meta；exist_pid + DB 已逐筆寫，
    # 這裡只是把每組 cookie_used / requires_cookie 等 url_meta 變動推到 DB。
    _STEP4_URL_META_FLUSH_EVERY = 10

    def _maybe_flush_url_meta_periodically(self, done_count: int) -> None:
        """Best-effort periodic url_meta DB sync. Idempotent under concurrent calls."""
        try:
            every = int(self._STEP4_URL_META_FLUSH_EVERY)
        except Exception:
            every = 10
        if every <= 0 or done_count <= 0:
            return
        if done_count % every != 0:
            return
        with contextlib.suppress(Exception):
            self._persist_url_meta()

    def _sync_exist_pid_to_db(self):
        """Bulk-sync the current exist_pid set into the canonical DB.

        Replaces the old ``_refresh_and_write_exist_pid`` which additionally
        scanned the download folder and wrote a JSON file.  Now that the DB
        is the sole source of truth, we only need the sentinel import.
        """
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            with contextlib.suppress(Exception):
                db.import_downloaded_set(self.exist_pid)
