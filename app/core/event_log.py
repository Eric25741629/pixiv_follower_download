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
import re
import sys
import threading
import time
from typing import IO

EVENTS_DIRNAME = "events"
_FILENAME_FMT = "events-{date}.jsonl"

# Filename scheme: 'events-YYYYMMDD.jsonl' is the day's first/oldest chunk
# (sequence 0, kept bare for back-compat); same-day size-based rotation adds
# 'events-YYYYMMDD.NNN.jsonl' (zero-padded sequence >= 1).
_EVENTS_RE = re.compile(r"^events-(\d{8})(?:\.(\d+))?\.jsonl$")

# Anchors that bound crash recovery: everything physically AFTER the most recent
# one is an orphan to re-apply. 'checkpoint' is emitted at startup (after
# recover_tail) so the recoverable window is always one session, never the whole
# log. session.start is deliberately NOT a cutoff — orphans from several crashed
# sessions can precede it.
_CUTOFF_ANCHOR_KINDS = frozenset({"session.shutdown", "snapshot", "checkpoint"})
# Structural kinds carrying no DB mutation (skipped when applying).
_NON_MUTATION_KINDS = frozenset(
    {"session.start", "session.shutdown", "snapshot", "checkpoint"}
)
# Rare anchor kinds that always force an fsync regardless of the batch cadence.
_FSYNC_ALWAYS_KINDS = frozenset(
    {"session.start", "session.shutdown", "snapshot", "checkpoint"}
)
# Bound the unclean-detection reverse scan so a multi-GB current-day file cannot
# stall startup: if no session.* marker is found within this many bytes from the
# end, conservatively treat the session as unclean (triggers recovery).
_DETECT_MAX_SCAN_BYTES = 16 * 1024 * 1024  # 16 MB


def _now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _today_key() -> str:
    return datetime.datetime.now().strftime("%Y%m%d")


def _event_filename(date: str, seq: int) -> str:
    """Build an event-log filename. seq <= 0 -> bare 'events-DATE.jsonl' (the
    historical name); seq >= 1 -> 'events-DATE.NNN.jsonl' for same-day rotation."""
    if seq <= 0:
        return _FILENAME_FMT.format(date=date)
    return f"events-{date}.{seq:03d}.jsonl"


def _parse_event_filename(name: str):
    """Return (date_str, seq) for an event-log filename, or None. A bare
    'events-DATE.jsonl' is sequence 0 (the day's first/oldest chunk)."""
    m = _EVENTS_RE.match(name)
    if not m:
        return None
    return (m.group(1), int(m.group(2)) if m.group(2) is not None else 0)


def _sorted_event_files(log_dir: str, *, reverse: bool = False) -> list[str]:
    """Event-log filenames sorted chronologically by (date, seq).

    Uses parsed keys, NOT lexical order: '.001' must sort AFTER the bare
    same-day file, which a plain string sort gets wrong because '.' < 'j'.
    """
    try:
        items = []
        for n in os.listdir(log_dir):
            key = _parse_event_filename(n)
            if key is not None:
                items.append((key, n))
    except OSError:
        return []
    items.sort(key=lambda x: x[0], reverse=reverse)
    return [n for _, n in items]


def _iter_lines_reverse(path: str, *, block_size: int = 65536, max_bytes: int | None = None):
    """Yield text lines from ``path`` newest-first (from EOF backward).

    Streams fixed-size blocks with a partial-line carry, so a single line longer
    than ``block_size`` (e.g. a fat pages.upsert_bulk record) is still
    reassembled and yielded whole. When ``max_bytes`` is set, stops after
    reading that many bytes from the end (bounds startup scans); callers compare
    ``os.path.getsize(path)`` to ``max_bytes`` to distinguish 'reached start of
    file' from 'hit the budget'. Decodes UTF-8 with replacement so a torn final
    record from a crash never raises.
    """
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        carry = b""
        read = 0
        while pos > 0:
            chunk = min(block_size, pos)
            pos -= chunk
            f.seek(pos)
            block = f.read(chunk)
            read += chunk
            data = block + carry
            parts = data.split(b"\n")
            carry = parts[0]  # fragment continued in an earlier (not-yet-read) block
            for piece in reversed(parts[1:]):
                yield piece.decode("utf-8", "replace")
            if max_bytes is not None and read >= max_bytes:
                return  # dangling carry is a partial line at the boundary; drop it
        if carry:
            yield carry.decode("utf-8", "replace")


class EventLog:
    """Thread-safe append-only JSONL writer."""

    def __init__(
        self,
        base_path: str,
        *,
        retention_days: int = 60,
        fsync_every_n: int = 1,
        fsync_interval_sec: float = 0.0,
        max_total_bytes: int = 0,
        rotate_size_bytes: int = 0,
    ):
        """Append-only event log writer.

        Durability cadence (the perf-vs-power-loss knob): an fsync is forced
        every ``fsync_every_n`` events OR every ``fsync_interval_sec`` seconds,
        whichever comes first, plus unconditionally on anchor kinds
        (session.* / snapshot / checkpoint) and on close(). Defaults
        (fsync_every_n=1, interval=0) reproduce the legacy per-event fsync;
        the app passes batched values (e.g. 200 / 1.0s) so the common path no
        longer pays a disk barrier per DB mutation. Lines are always flush()'d,
        so a process kill never loses already-written events — only a true OS
        crash / power cut in the un-fsync'd window can, the same tier SQLite's
        WAL+synchronous=NORMAL already accepts.

        ``rotate_size_bytes`` (>0) rolls the current day's file to the next
        sequence once it exceeds that size. ``max_total_bytes`` (>0) caps the
        events directory: GC deletes oldest files until under the cap, never
        crossing the most recent snapshot/shutdown/checkpoint anchor file.
        """
        self._dir = os.path.join(base_path, EVENTS_DIRNAME)
        os.makedirs(self._dir, exist_ok=True)
        self._retention_days = int(retention_days)
        self._fsync_every_n = max(1, int(fsync_every_n))
        self._fsync_interval_sec = max(0.0, float(fsync_interval_sec))
        self._max_total_bytes = max(0, int(max_total_bytes))
        self._rotate_size_bytes = max(0, int(rotate_size_bytes))
        self._lock = threading.Lock()
        self._fh: IO | None = None
        self._current_date: str | None = None
        self._cur_seq: int = 0
        self._cur_size: int = 0
        self._cur_basename: str | None = None
        self._events_since_fsync: int = 0
        self._last_fsync_at: float = time.monotonic()
        self._closed: bool = False
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
            if self._closed:
                return
            self._write_locked(kind, **fields)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._fh is None:
                self._closed = True
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
                self._closed = True

    def compact_before_date(self, cutoff_date: str) -> int:
        """Delete whole event files dated strictly before ``cutoff_date`` (YYYYMMDD).

        Called after a verified, fsync'd DB snapshot: a file whose entire content
        predates the snapshot is redundant because that state is captured in the
        history/ snapshot image. Never deletes the current file nor any file
        at/after the most recent snapshot/shutdown/checkpoint anchor (the replay
        base for tools/replay_events.py). Returns the count removed.
        """
        removed = 0
        with self._lock:
            files = _sorted_event_files(self._dir)
            protected = self._anchor_protected_basenames(files)
            for name in files:
                if name == self._cur_basename or name in protected:
                    continue
                key = _parse_event_filename(name)
                if key is None or key[0] >= cutoff_date:
                    continue
                try:
                    os.remove(os.path.join(self._dir, name))
                    removed += 1
                except OSError:
                    pass
        return removed

    def _detect_unclean(self) -> bool:
        """Scan existing event files for a trailing session.shutdown.

        Returns:
            True  if the last session.* event found is session.start
                  (no matching shutdown after it — indicates a crash).
            False if the last session.* event is session.shutdown, or
                  if no events exist at all (first run).
        """
        for name in _sorted_event_files(self._dir, reverse=True):
            path = os.path.join(self._dir, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            try:
                # Reverse, block-streamed read bounded to the tail — never
                # readlines() a multi-GB file into RAM.
                for ln in _iter_lines_reverse(path, max_bytes=_DETECT_MAX_SCAN_BYTES):
                    s = ln.strip()
                    if not s:
                        continue
                    try:
                        ev = json.loads(s)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    kind = ev.get("k", "")
                    if kind == "session.shutdown":
                        return False
                    if kind == "session.start":
                        return True
            except OSError:
                continue
            # Scanned the whole tail budget without a session.* marker. If the
            # file is larger than the budget we only saw its end, so there is
            # un-anchored data after the last marker → conservatively unclean.
            if size > _DETECT_MAX_SCAN_BYTES:
                return True
            # Otherwise the file was fully scanned with no marker → look further
            # back in older files.
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
        # Continue the day's highest existing sequence so same-day restarts
        # append to the live chunk; fall back to sequence 0 (the bare filename).
        seq = 0
        for name in _sorted_event_files(self._dir):
            key = _parse_event_filename(name)
            if key is not None and key[0] == today and key[1] > seq:
                seq = key[1]
        basename = _event_filename(today, seq)
        path = os.path.join(self._dir, basename)
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self._current_date = today
        self._cur_seq = seq
        self._cur_basename = basename
        try:
            self._cur_size = os.path.getsize(path)
        except OSError:
            self._cur_size = 0
        if prev_date is not None:  # only GC on real date-change rotation
            self._gc_old_files_locked()

    def _rotate_for_size_locked(self) -> None:
        """Roll to the next same-day sequence once the current file exceeds
        rotate_size_bytes, so no single file dwarfs the recovery/GC budget.
        No-op when rotation is disabled or the file is under the cap."""
        if self._rotate_size_bytes <= 0 or self._fh is None:
            return
        if self._cur_size < self._rotate_size_bytes:
            return
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except OSError:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None
        self._cur_seq += 1
        basename = _event_filename(self._current_date or _today_key(), self._cur_seq)
        path = os.path.join(self._dir, basename)
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self._cur_basename = basename
        self._cur_size = 0
        self._events_since_fsync = 0
        self._last_fsync_at = time.monotonic()

    def _anchor_protected_basenames(self, files: list[str]) -> set:
        """Return basenames at/after the most recent file that contains a
        snapshot/shutdown/checkpoint anchor — these are the replay base and must
        never be byte-GC'd. If no anchor exists anywhere, protect everything
        (the conservative, data-safe default)."""
        for idx in range(len(files) - 1, -1, -1):  # newest first
            path = os.path.join(self._dir, files[idx])
            try:
                has_anchor = False
                for ln in _iter_lines_reverse(path, max_bytes=_DETECT_MAX_SCAN_BYTES):
                    s = ln.strip()
                    if not s:
                        continue
                    try:
                        ev = json.loads(s)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if ev.get("k") in _CUTOFF_ANCHOR_KINDS:
                        has_anchor = True
                        break
                if has_anchor:
                    return set(files[idx:])
            except OSError:
                continue
        return set(files)

    def _gc_old_files_locked(self) -> None:
        # (1) Time-based retention: drop files older than retention_days, never
        # the currently-open file.
        if self._retention_days > 0:
            cutoff = datetime.datetime.now().timestamp() - self._retention_days * 86400
            for name in _sorted_event_files(self._dir):
                if name == self._cur_basename:
                    continue  # never GC the currently-open file
                path = os.path.join(self._dir, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except OSError:
                    pass
        # (2) Byte-capped retention: delete oldest-first until under the cap,
        # but never the current file and never a file at/after the most recent
        # snapshot/shutdown/checkpoint anchor (those are the replay base; losing
        # them makes auto-recovery / tools/replay_events.py silently incomplete).
        if self._max_total_bytes <= 0:
            return
        files = _sorted_event_files(self._dir)  # oldest first
        protected = self._anchor_protected_basenames(files)
        sizes: dict[str, int] = {}
        total = 0
        for name in files:
            try:
                sz = os.path.getsize(os.path.join(self._dir, name))
            except OSError:
                continue
            sizes[name] = sz
            total += sz
        for name in files:  # oldest first
            if total <= self._max_total_bytes:
                break
            if name == self._cur_basename or name in protected:
                continue
            try:
                os.remove(os.path.join(self._dir, name))
                total -= sizes.get(name, 0)
            except OSError:
                pass

    def _write_locked(self, kind: str, **fields) -> None:
        self._open_today_locked()       # cheap; no-op if same day
        self._rotate_for_size_locked()  # roll to next sequence if over size cap
        if self._fh is None:
            return
        payload = {"t": _now_iso(), "k": kind}
        for k, v in fields.items():
            if v is not None:
                payload[k] = v
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            self._fh.write(line)
            # Always flush: bytes reach the OS page cache, so a process kill
            # (SIGKILL) never loses an already-written line. fsync is the
            # expensive disk barrier and is batched below.
            self._fh.flush()
            self._cur_size += len(line.encode("utf-8"))
            self._events_since_fsync += 1
            now = time.monotonic()
            force = kind in _FSYNC_ALWAYS_KINDS
            if (force
                    or self._events_since_fsync >= self._fsync_every_n
                    or (self._fsync_interval_sec > 0.0
                        and now - self._last_fsync_at >= self._fsync_interval_sec)):
                os.fsync(self._fh.fileno())
                self._events_since_fsync = 0
                self._last_fsync_at = now
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

    def _pages_downloaded_bulk(db, e):
        db.mark_pages_downloaded_bulk(e.get("rows", []))

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
            upload_date=e.get("upload_date"),
            create_date=e.get("create_date"),
            user_id=e.get("user_id"),
            user_name=e.get("user_name"),
        )

    def _artwork_discovered(db, e):
        db.upsert_artworks(e.get("pids", []),
                           discovered_at=e.get("discovered_at"),
                           user_id=e.get("user_id"))

    def _artwork_user_id_backfill(db, e):
        db.backfill_user_ids(e.get("pids", []), e.get("user_id"))

    def _artwork_revoked(db, e):
        db.mark_artwork_revoked(pid=e["pid"], revoked_at=e.get("revoked_at"))

    def _artwork_imported_set(db, e):
        db.import_downloaded_set(set(e.get("pids", [])))

    def _noop(db, e):
        return

    return {
        "page.upsert": _page_upsert,
        "page.downloaded": _page_downloaded,
        "page.failed": _page_failed,
        "page.pending": _page_pending,
        "pages.upsert_bulk": _pages_upsert_bulk,
        "pages.downloaded_bulk": _pages_downloaded_bulk,
        "artwork.upsert": _artwork_upsert,
        "artwork.discovered": _artwork_discovered,
        "artwork.user_id_backfill": _artwork_user_id_backfill,
        "artwork.revoked": _artwork_revoked,
        "artwork.imported_set": _artwork_imported_set,
        "session.start": _noop,
        "session.shutdown": _noop,
        "snapshot": _noop,
        "checkpoint": _noop,
    }


def _iter_events(log_dir: str):
    """Yield events from all events-*.jsonl files in chronological order."""
    names = _sorted_event_files(log_dir)
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

    Restore snapshot first (via SQLite backup API), then apply every event whose
    timestamp is >= the snapshot's (a strict '<' cutoff for *skipping*). The
    boundary millisecond is re-applied rather than skipped because 't' is only
    millisecond-resolution: an event emitted in the same millisecond as the
    snapshot but AFTER the backup ran must not be dropped, and re-applying is
    idempotent. If no snapshot is provided, apply everything from the start of
    the log.
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
            if snapshot_ts and ev.get("t", "") < snapshot_ts:
                result.skipped_pre_snapshot += 1
            else:
                result.applied += 1
        return result

    db = MetadataDB(os.path.dirname(db_path) or ".")
    dispatch = _dispatch_table()
    try:
        for ev in _iter_events(log_dir):
            # Strict '<': re-apply the snapshot's own millisecond instead of
            # skipping it. 't' is millisecond-resolution, so a mutation emitted
            # in the same ms as the snapshot but AFTER the backup ran would be
            # dropped by '<='. Re-applying the boundary ms is safe (every
            # dispatched mutation is INSERT OR IGNORE / ON CONFLICT idempotent),
            # whereas dropping a post-snapshot event silently loses data.
            if snapshot_ts and ev.get("t", "") < snapshot_ts:
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


# Bound on the orphan TAIL scanned at startup (not the total log). recover_tail
# reverse-seeks from the newest file to the most recent cutoff anchor, so it
# normally reads only the bytes since the last anchor. This cap is the safety
# stop for the pathological case where no recent anchor exists.
RECOVER_TAIL_MAX_BYTES = 256 * 1024 * 1024  # 256 MB


def recover_tail(db, log_dir: str) -> int:
    """Apply orphan events from the most recent unclean session into a live DB.

    Orphan events = everything physically AFTER the most recent cutoff anchor
    (session.shutdown / snapshot / checkpoint). Found by a reverse, block-
    streamed scan from the newest file backward, so cost is O(tail) — the bytes
    since the last anchor — not O(total log). Returns the count applied.

    Idempotent: all dispatched mutations are INSERT OR IGNORE / ON CONFLICT DO
    UPDATE, so re-application (or replaying one extra event) is harmless; the
    only real risk is UNDER-applying, guarded by walking older files until an
    anchor is found and by applying the whole collected tail when none is. The
    DB's event_log is temporarily set to None during apply to avoid an
    emit-loop.

    The reverse scan is bounded to RECOVER_TAIL_MAX_BYTES; if no anchor is found
    within that budget the most-recent budget-worth of orphans is still applied
    (best effort, idempotent) and a warning is logged — far better than the old
    behaviour of skipping recovery entirely on a large backlog.
    """
    dispatch = _dispatch_table()

    # Collect orphan events newest-first until the most recent cutoff anchor.
    tail: list = []
    scanned = 0
    hit_anchor = False
    over_budget = False
    for name in _sorted_event_files(log_dir, reverse=True):
        if hit_anchor or over_budget:
            break
        path = os.path.join(log_dir, name)
        try:
            for ln in _iter_lines_reverse(path):
                s = ln.strip()
                if not s:
                    continue
                scanned += len(s) + 1
                try:
                    ev = json.loads(s)
                except (json.JSONDecodeError, ValueError):
                    continue
                kind = ev.get("k", "")
                if kind in _CUTOFF_ANCHOR_KINDS:
                    hit_anchor = True
                    break
                if kind in _NON_MUTATION_KINDS:
                    continue  # e.g. session.start: not a cutoff, carries no mutation
                tail.append(ev)
                if scanned > RECOVER_TAIL_MAX_BYTES:
                    over_budget = True
                    break
        except OSError:
            continue

    if over_budget:
        logging.getLogger("pixiv.event_log").warning(
            "recover_tail: no clean anchor within %.0f MB; applying the most "
            "recent %d orphan events best-effort. Run tools/replay_events.py "
            "for a full offline rebuild if the live DB looks stale.",
            RECOVER_TAIL_MAX_BYTES / 1048576, len(tail),
        )

    # Apply in forward (chronological) order; suppress emit to avoid a log loop.
    saved = getattr(db, "_event_log", None)
    if hasattr(db, "_event_log"):
        db._event_log = None
    applied = 0
    try:
        for ev in reversed(tail):
            handler = dispatch.get(ev.get("k", ""))
            if handler is None:
                continue
            try:
                handler(db, ev)
                applied += 1
            except Exception:
                logging.getLogger("pixiv.event_log").exception(
                    "recover_tail dispatch error for %s", ev.get("k"),
                )
    finally:
        if hasattr(db, "_event_log"):
            db._event_log = saved
    return applied
