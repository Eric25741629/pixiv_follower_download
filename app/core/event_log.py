"""Append-only JSONL event log.

Records every mutation to MetadataDB canonical tables so the DB can be
rebuilt (manually via replay() / CLI) or repaired (automatically via
recover_tail() at app startup) after a crash.

See docs/superpowers/specs/2026-05-23-event-log-design.md.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import threading
from typing import IO

EVENTS_DIRNAME = "events"
_FILENAME_FMT = "events-{date}.jsonl"


def _now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _today_key() -> str:
    return datetime.datetime.now().strftime("%Y%m%d")


class EventLog:
    """Thread-safe append-only JSONL writer."""

    def __init__(self, base_path: str, *, retention_days: int = 60):
        self._dir = os.path.join(base_path, EVENTS_DIRNAME)
        os.makedirs(self._dir, exist_ok=True)
        self._retention_days = int(retention_days)
        self._lock = threading.Lock()
        self._fh: IO | None = None
        self._current_date: str | None = None
        # Must run before opening today's file so this session's own
        # session.start is not yet written when we scan for prior sessions.
        self.last_session_was_unclean = self._detect_unclean()
        with self._lock:
            self._open_today_locked()
            self._gc_old_files_locked()  # explicit startup scan
            self._write_locked("session.start", pid=os.getpid(),
                               python_version=sys.version.split()[0])

    @property
    def log_dir(self) -> str:
        return self._dir

    def emit(self, kind: str, **fields) -> None:
        """Append one event. Synchronous flush + fsync. Thread-safe."""
        with self._lock:
            self._write_locked(kind, **fields)

    def close(self) -> None:
        with self._lock:
            if self._fh is None:
                return
            try:
                self._write_locked("session.shutdown", clean=True)
            finally:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
                self._current_date = None

    def _detect_unclean(self) -> bool:
        """Scan existing event files for a trailing session.shutdown.

        Returns:
            True  if the last session.* event found is session.start
                  (no matching shutdown after it — indicates a crash).
            False if the last session.* event is session.shutdown, or
                  if no events exist at all (first run).
        """
        try:
            files = sorted(
                [n for n in os.listdir(self._dir)
                 if n.startswith("events-") and n.endswith(".jsonl")],
                reverse=True,
            )
        except OSError:
            return False
        for name in files:
            path = os.path.join(self._dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for ln in reversed(lines):
                try:
                    ev = json.loads(ln)
                except (json.JSONDecodeError, ValueError):
                    continue
                kind = ev.get("k", "")
                if kind == "session.shutdown":
                    return False
                if kind == "session.start":
                    return True
        return False  # no session.* events found → first run

    def _open_today_locked(self) -> None:
        today = _today_key()
        if self._fh is not None and self._current_date == today:
            return
        prev_date = self._current_date  # None on first open after construction or close()
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
        path = os.path.join(self._dir, _FILENAME_FMT.format(date=today))
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self._current_date = today
        if prev_date is not None:  # only GC on real date-change rotation
            self._gc_old_files_locked()

    def _gc_old_files_locked(self) -> None:
        if self._retention_days <= 0:
            return
        cutoff = datetime.datetime.now().timestamp() - self._retention_days * 86400
        try:
            for name in os.listdir(self._dir):
                if not (name.startswith("events-") and name.endswith(".jsonl")):
                    continue
                if self._current_date and name == _FILENAME_FMT.format(date=self._current_date):
                    continue  # never GC the currently-open file
                path = os.path.join(self._dir, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except OSError:
                    pass
        except OSError:
            pass

    def _write_locked(self, kind: str, **fields) -> None:
        self._open_today_locked()  # cheap; no-op if same day
        if self._fh is None:
            return
        payload = {"t": _now_iso(), "k": kind}
        for k, v in fields.items():
            if v is not None:
                payload[k] = v
        try:
            self._fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except OSError as e:
            logging.getLogger("pixiv.event_log").warning(
                "event log write failed: %s (kind=%s)", e, kind,
            )


# ── Replay infrastructure ─────────────────────────────────────────────────────

from dataclasses import dataclass, field


@dataclass
class ReplayResult:
    applied: int = 0
    skipped_pre_snapshot: int = 0
    errors: list = field(default_factory=list)


def _dispatch_table():
    """Return {kind: callable(db, event_dict)}.

    Defined lazily so tests can import event_log without MetadataDB.
    """
    def _page_upsert(db, e):
        db.upsert_page(
            pid=e["pid"], page_index=e["page_index"], status=e["status"],
            url=e.get("url"), file_path=e.get("file_path"),
            file_size=e.get("file_size"),
            downloaded_at=e.get("downloaded_at"),
            last_attempted_at=e.get("last_attempted_at"),
            failure_reason=e.get("failure_reason"),
            bump_attempt=bool(e.get("bump_attempt", False)),
        )

    def _page_downloaded(db, e):
        db.mark_page_downloaded(
            pid=e["pid"], page_index=e["page_index"],
            file_path=e.get("file_path"), file_size=e.get("file_size"),
            url=e.get("url"),
        )

    def _page_failed(db, e):
        db.mark_page_failed(
            pid=e["pid"], page_index=e["page_index"],
            failure_reason=e.get("reason", "unknown"),
            url=e.get("url"),
        )

    def _page_pending(db, e):
        db.mark_page_pending(pid=e["pid"], page_index=e["page_index"], url=e.get("url"))

    def _pages_upsert_bulk(db, e):
        db.upsert_pages_bulk(e.get("rows", []))

    def _artwork_upsert(db, e):
        db.upsert_artwork(
            pid=e["pid"],
            discovered_at=e.get("discovered_at"),
            page_count=e.get("page_count"),
            like_count=e.get("like_count"),
            tags=e.get("tags"),
            img_url_template=e.get("img_url_template"),
            requires_cookie=e.get("requires_cookie"),
            meta_updated_at=e.get("meta_updated_at"),
            revoked_at=e.get("revoked_at"),
        )

    def _artwork_discovered(db, e):
        db.upsert_artworks(e.get("pids", []),
                           discovered_at=e.get("discovered_at"))

    def _artwork_revoked(db, e):
        db.mark_artwork_revoked(pid=e["pid"], revoked_at=e.get("revoked_at"))

    def _noop(db, e):
        return

    return {
        "page.upsert": _page_upsert,
        "page.downloaded": _page_downloaded,
        "page.failed": _page_failed,
        "page.pending": _page_pending,
        "pages.upsert_bulk": _pages_upsert_bulk,
        "artwork.upsert": _artwork_upsert,
        "artwork.discovered": _artwork_discovered,
        "artwork.revoked": _artwork_revoked,
        "session.start": _noop,
        "session.shutdown": _noop,
        "snapshot": _noop,
    }


def _iter_events(log_dir: str):
    """Yield events from all events-*.jsonl files in chronological order."""
    try:
        names = sorted(
            n for n in os.listdir(log_dir)
            if n.startswith("events-") and n.endswith(".jsonl")
        )
    except OSError:
        return
    for name in names:
        path = os.path.join(log_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        yield json.loads(ln)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue


def replay(
    db_path: str,
    log_dir: str,
    *,
    snapshot_path: str | None = None,
    dry_run: bool = False,
) -> ReplayResult:
    """Rebuild a DB at db_path from snapshot (optional) + all events in log_dir.

    Restore snapshot first (via SQLite backup API), then apply every event with
    timestamp > snapshot's. If no snapshot is provided, apply everything from
    the start of the log.
    """
    import sqlite3
    from app.core.metadata_db import DB_FILENAME, MetadataDB

    assert os.path.basename(db_path) == DB_FILENAME, (
        f"replay db_path must end in {DB_FILENAME}; got {db_path!r}"
    )

    snapshot_ts: str | None = None
    if snapshot_path and os.path.isfile(snapshot_path):
        if not dry_run:
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            src = sqlite3.connect(snapshot_path, timeout=10.0, isolation_level=None)
            try:
                dst = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
        # Find the snapshot event in the log that pointed at this backup.
        for ev in _iter_events(log_dir):
            if ev.get("k") == "snapshot" and ev.get("backup_path", "").endswith(
                os.path.basename(snapshot_path)
            ):
                snapshot_ts = ev.get("t")

    result = ReplayResult()
    if dry_run:
        for ev in _iter_events(log_dir):
            if snapshot_ts and ev.get("t", "") <= snapshot_ts:
                result.skipped_pre_snapshot += 1
            else:
                result.applied += 1
        return result

    db = MetadataDB(os.path.dirname(db_path) or ".")
    dispatch = _dispatch_table()
    try:
        for ev in _iter_events(log_dir):
            if snapshot_ts and ev.get("t", "") <= snapshot_ts:
                result.skipped_pre_snapshot += 1
                continue
            handler = dispatch.get(ev.get("k", ""))
            if handler is None:
                result.errors.append(f"unknown kind: {ev.get('k')!r}")
                continue
            try:
                handler(db, ev)
                result.applied += 1
            except Exception as exc:
                result.errors.append(f"{ev.get('k')}: {exc}")
    finally:
        db.close()
    return result
