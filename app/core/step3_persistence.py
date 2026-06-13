"""Disk/DB flush + pending-PID / revoked-PID tracking for ``get_img_url_thread``.

The ``all_url.txt`` snapshot writer, the metadata-DB mirror, the pending-PID
tracker (``pictures_id.txt``), the per-PID processed/resolved-meta persistence
(B3/B6 crash-safe path), the revoked-PID file, and the trailing
queue-to-textfile drains — mixed into ``get_img_url_thread`` via
``_Step3PersistenceMixin``. Every method uses only ``self.`` for cross-method
calls (resolved through inheritance) plus the module-level names imported
below, so behavior is byte-for-byte identical to the originals. Sibling helpers
these methods call but do not own (``self._get_meta``, ``self._to_int``,
``self._diag``, ``self._lookup_url_meta_entry``,
``self._meta_has_usable_url_and_pages``, ``self._step3_build_pid_download_urls``)
live on the concrete class or its other mixins.
"""
from __future__ import annotations

import os

from app.core.metadata_db import emit_db_stats, open_metadata_db
from app.core.pixiv_thread_utils import (
    atomic_write_text,
    canonicalize_pximg_url_for_storage,
    mirror_meta_dict_to_db,
    normalize_pid,
    normalize_pid_set,
    write_all_url_file,
)
from app.core.worker_event import WorkerEvent


class _Step3PersistenceMixin:
    """all_url / metadata-DB / pending-PID / revoked-PID persistence."""

    def _mark_revoked_pid(self, pid, reason="404"):
        pid_key = normalize_pid(pid)
        if not pid_key:
            return
        if pid_key in self._revoked_pid_set:
            return
        self._revoked_pid_set.add(pid_key)
        self._revoked_pid_new.add(pid_key)
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            try:
                db.mark_artwork_revoked(pid_key)
            except Exception:
                pass
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='orange'>PID {pid_key} 已標記為失效（{reason}），後續會自動略過</font></p>"
            ))
        except Exception:
            pass

    def _flush_revoked_pid_file(self):
        try:
            if not self._revoked_pid_set:
                return
            all_pids = sorted(self._revoked_pid_set)
            try:
                atomic_write_text(self.revoked_pid_path, all_pids, backup=True)
            except Exception:
                with open(self.revoked_pid_path, "w", encoding="utf-8") as f:
                    f.writelines([str(x) + "\n" for x in all_pids])
        except Exception:
            pass

    def _extract_pid_from_url(self, url):
        try:
            filename = str(url).rsplit('/', 1)[1]
            return str(filename.split('_', 1)[0])
        except Exception:
            return None

    def _init_metadata_db(self, json_meta):
        """Open the SQLite metadata cache; first-run import from JSON."""
        return open_metadata_db(self.path, json_meta, event_log=getattr(self, "_event_log", None))

    def _emit_metadata_db_stats(self, stage="Step3"):
        """Print a one-liner with current SQLite cache size."""
        emit_db_stats(getattr(self, "_metadata_db", None), self._q, stage=stage)

    def _mirror_url_meta_to_db(self):
        """Best-effort bulk-mirror of self.url_meta into the SQLite cache."""
        mirror_meta_dict_to_db(getattr(self, "_metadata_db", None), self.url_meta)

    def _flush_url_meta_snapshot(self):
        self._mirror_url_meta_to_db()

    def _persist_url_meta_with_fallback(self):
        self._mirror_url_meta_to_db()

    def _write_all_url_file(self, urls, reason="unknown"):
        return write_all_url_file(self.path, urls, reason=reason, diag=self._diag)

    def _read_existing_all_url_lines(self):
        """Read the current all_url.txt; return [] on any error."""
        path = os.path.join(self.path, "all_url.txt")
        try:
            with open(path, encoding='utf-8') as f:
                return [line.rstrip() for line in f if line.rstrip()]
        except Exception:
            return []

    def _filter_undownloaded_urls(self, urls, *, only_https=False):
        """Drop URLs whose PID is already in exist_pid; canonicalise the rest."""
        out = []
        for u in urls:
            if only_https and not (isinstance(u, str) and 'https' in u):
                continue
            pid = self._extract_pid_from_url(u)
            if pid is not None and pid in self.exist_pid:
                continue
            out.append(canonicalize_pximg_url_for_storage(u))
        return out

    @staticmethod
    def _dedupe_preserving_order(items):
        """Return items deduplicated, preserving first-seen order."""
        seen = set()
        out = []
        for x in items:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    def _write_all_url_snapshot(self, fetched_urls, *, full=False):
        """Write merged all_url snapshot to disk and sync the DELTA to the DB.

        ``full=True`` (terminal flushes) pushes the entire merged set once as a
        backstop; periodic flushes push only URLs not yet mirrored this run.
        """
        try:
            old_urls = self._filter_undownloaded_urls(self._read_existing_all_url_lines())
            new_urls = self._filter_undownloaded_urls(fetched_urls, only_https=True)
            merged = self._dedupe_preserving_order(old_urls + new_urls)
            try:
                os.makedirs(self.path, exist_ok=True)
            except Exception:
                pass
            write_ok = self._write_all_url_file(merged, reason="step3_snapshot")
            self._diag(
                "step3_snapshot_merged",
                old_count=len(old_urls),
                new_count=len(new_urls),
                merged_count=len(merged),
                write_ok=bool(write_ok),
            )
            db = getattr(self, "_metadata_db", None)
            if db is not None:
                try:
                    # QW1: only mirror URLs not already pushed THIS run. The full
                    # all_url.txt snapshot above is unchanged; upsert_pending_urls
                    # is INSERT OR IGNORE (idempotent), so re-deriving the full set
                    # from all_url.txt on the next run loses nothing. This turns
                    # the per-batch DB write + pages.upsert_bulk event from
                    # O(cumulative) into O(new) and collapses the quadratic
                    # event-log growth at the source.
                    flushed = getattr(self, "_flushed_urls", None)
                    if flushed is None:
                        flushed = set()
                        self._flushed_urls = flushed
                    to_push = merged if full else [u for u in merged if u not in flushed]
                    if to_push:
                        entries = [
                            (url, self._extract_pid_from_url(url) or "")
                            for url in to_push
                        ]
                        db.upsert_pending_urls(entries)
                        flushed.update(to_push)
                except Exception:
                    pass
            return old_urls, new_urls, merged
        except Exception as err:
            self._diag("step3_snapshot_failed", error=str(err))
            return [], [], []

    def _resolve_pictures_id_file_path(self):
        candidates = [os.path.join(self.path, "pictures_id.txt")]
        try:
            appdata_path = os.path.join(os.getenv('APPDATA') + r'/pixiv_download/', 'pictures_id.txt')
            if appdata_path not in candidates:
                candidates.append(appdata_path)
        except Exception:
            pass
        for p in candidates:
            try:
                if os.path.isfile(p):
                    return p
            except Exception:
                pass
        return candidates[0]

    def _persist_pending_pid_file(self):
        """Flush the pending-PID set to disk.

        Phase 36: this used to be called per-PID with backup=True, costing
        O(N^2) total disk I/O (sort + atomic_write_text + shutil.copy2 of the
        whole file each time). It now runs only on the every-100-PID batch
        flush in ``_run_processing_loop`` and on finalize, with backup=False
        because pictures_id.txt is a runtime pending-list, not user data
        worth keeping in history/.
        """
        try:
            lines = sorted(
                [str(x) for x in (self._pending_pid_remaining or set()) if str(x).strip()],
                key=lambda s: int(s) if str(s).isdigit() else str(s),
            )
            atomic_write_text(self._pending_pid_file_path, lines, backup=False)
        except Exception:
            pass

    def _init_pending_pid_tracker(self, fallback_pid_list, reset_with_fallback=False):
        self._pending_pid_file_path = self._resolve_pictures_id_file_path()
        loaded = set()
        if not bool(reset_with_fallback):
            try:
                with open(self._pending_pid_file_path, encoding='utf-8', errors='ignore') as f:
                    loaded = normalize_pid_set([line.rstrip() for line in f if str(line).strip()])
            except Exception:
                loaded = set()
        if not loaded:
            loaded = normalize_pid_set(fallback_pid_list)
        self._pending_pid_remaining = set(loaded)
        self._persist_pending_pid_file()

    def _mark_pid_processed(self, pid, result=None):
        """Record the per-PID outcome durably and crash-safely.

        ``result`` is the value ``get_download_url`` returned for this PID:

        - list with http URLs  -> RESOLVED: persist ``meta_updated_at`` together
          with ``page_count`` and the ``pages`` rows in one shot (see
          :meth:`_persist_resolved_pid_meta`) so a crash can never strand the PID
          with meta marked done but no page rows.
        - non-empty list with no URL (the ``[str(pid)]`` error sentinel from an
          empty/transient payload) -> keep the PID PENDING for retry; do NOT
          discard it or stamp ``meta_updated_at``.
        - ``[]`` (exist_pid skip / filtered-out) or ``None`` (legacy/combined
          callers) -> processed-with-nothing-to-download: drop from the tracker
          and ``mark_pid_done`` so it isn't re-queried.

        Phase 36: no per-PID disk write here (the all_url snapshot still batches
        on the every-25-PID boundary); only the DB writes are per-PID, and those
        are idempotent.
        """
        pid_key = normalize_pid(pid)
        if not pid_key:
            return
        has_urls = isinstance(result, list) and any(
            isinstance(u, str) and "https" in u for u in result
        )
        is_transient_fail = (
            isinstance(result, list) and len(result) > 0 and not has_urls
            and pid_key not in self._revoked_pid_set
        )
        if is_transient_fail:
            # Empty/transient payload: leave the PID pending (both the in-memory
            # tracker and the DB) so it is re-queried next run instead of being
            # silently closed with no usable meta.
            return
        try:
            with self._pending_pid_lock:
                self._pending_pid_remaining.discard(pid_key)
        except Exception:
            pass
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return
        if pid_key in self._revoked_pid_set:
            return  # already closed via mark_artwork_revoked
        if has_urls and self._persist_resolved_pid_meta(pid_key):
            return  # meta + page rows committed together (crash-safe)
        try:
            db.mark_pid_done(pid_key)
        except Exception:
            pass

    def _persist_resolved_pid_meta(self, pid_key):
        """Durably persist a resolved PID's meta + page rows together.

        Seeds the ``pages`` rows first, then stamps ``artworks`` meta (which sets
        ``meta_updated_at`` and ``page_count`` atomically and emits the
        ``artwork.upsert`` event). Ordering pages-before-meta means a crash in
        between leaves the PID still pending rather than meta-done-without-pages.
        Returns True iff a usable meta entry was found and persisted.
        """
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return False
        meta = self._lookup_url_meta_entry(pid_key)
        if not meta or not self._meta_has_usable_url_and_pages(meta):
            return False
        try:
            img_url = str(meta.get("img_url"))
            pagecount = self._to_int(meta.get("pagecount", 0), 0) or 0
            built = self._step3_build_pid_download_urls(img_url, pagecount) or []
            if built:
                db.upsert_pending_urls([(u, pid_key) for u in built])
            tags = meta.get("tag")
            like = meta.get("like")
            db.upsert_meta(
                pid_key,
                tags=tags if isinstance(tags, list) else None,
                like_count=like if isinstance(like, int) else None,
                page_count=pagecount,
                img_url=img_url,
                requires_cookie=meta.get("requires_cookie"),
            )
            return True
        except Exception:
            return False

    def _persist_step3_url_meta(self):
        self._mirror_url_meta_to_db()

    def _drain_queue_to_text_file(self, queue, file_name, mode_append=True):
        """Drain a Queue and append/write its items to a text file.

        Uses safe_io.atomic_append_text / atomic_write_text first; falls back
        to plain file open. Both layers swallow errors — the original code
        treated these as best-effort.
        """
        items = [queue.get() for _ in range(queue.qsize())]
        target = os.path.join(self.path, file_name)
        try:
            if mode_append:
                from safe_io import atomic_append_text
                atomic_append_text(target, items)
            else:
                from safe_io import atomic_write_text
                atomic_write_text(target, [str(t) for t in items])
            return
        except Exception:
            pass
        try:
            mode = "a+" if mode_append else "w+"
            with open(target, mode) as f:
                f.writelines([str(text) + "\n" for text in items])
        except Exception:
            pass

    def _persist_step3_net_err(self, error_pid):
        """Write the error-PID list to net_err.txt (overwrite each run)."""
        target = os.path.join(self.path, "net_err.txt")
        try:
            from safe_io import atomic_write_text
            atomic_write_text(target, [str(t) for t in error_pid])
            return
        except Exception:
            pass
        try:
            with open(target, "w+") as f:
                for text in error_pid:
                    f.write(str(text) + '\n')
        except Exception:
            pass
