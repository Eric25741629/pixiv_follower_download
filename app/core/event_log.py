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
