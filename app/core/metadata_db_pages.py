"""``pages``-table CRUD + pending-URL helpers for :class:`MetadataDB`.

Extracted from ``metadata_db.py`` (file-size refactor). One row per
(PID, page) tuple — the download queue lives here. This module owns the
PAGE_STATUS_* constants and ``_VALID_PAGE_STATUSES`` (re-exported by
``metadata_db`` so existing ``from app.core.metadata_db import
PAGE_STATUS_PENDING`` callers are unchanged) plus the pages-table methods,
the ``pending_urls`` group, and the bulk download-mark helpers.

Mixed into ``MetadataDB`` via ``_PagesMixin``; the methods run against the
connection, lock, bulk-writer, PID coercion and event emitter
(``_conn`` / ``_lock`` / ``_bulk_write`` / ``_coerce_pid`` / ``_emit``)
provided by the concrete class. ``parse_pid_and_page_from_url`` is imported
lazily inside the methods (as in the original) to avoid an import cycle.
"""
from __future__ import annotations

from collections.abc import Iterable

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


class _PagesMixin:
    """``pages``-table CRUD + pending-URL helpers, mixed into ``MetadataDB``."""

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

    def page_status_counts(self) -> dict:
        """Aggregate ``pages`` rows by status — useful for UI / diagnostics."""
        cur = self._conn().execute(
            "SELECT status, COUNT(*) FROM pages GROUP BY status"
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}
