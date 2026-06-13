"""Pure event-log file iteration / naming helpers (file-size refactor).

Split out of ``event_log.py``: the filename scheme regex + builder/parser, the
chronological file lister, the reverse block-streamed line reader, and the
forward event iterator. These touch only the filesystem + ``json`` and hold no
``EventLog`` state, so they live here and are re-imported back into
``event_log`` (``from app.core.event_log_io import ...``) so existing
``from app.core.event_log import _iter_events, _iter_lines_reverse,
_parse_event_filename, _sorted_event_files`` callers (and the ``EventLog``
methods / ``replay`` / ``recover_tail`` that reference these as module globals)
are unchanged.

This module imports nothing from ``event_log`` — no import cycle.
"""
from __future__ import annotations

import json
import os
import re

_FILENAME_FMT = "events-{date}.jsonl"

# Filename scheme: 'events-YYYYMMDD.jsonl' is the day's first/oldest chunk
# (sequence 0, kept bare for back-compat); same-day size-based rotation adds
# 'events-YYYYMMDD.NNN.jsonl' (zero-padded sequence >= 1).
_EVENTS_RE = re.compile(r"^events-(\d{8})(?:\.(\d+))?\.jsonl$")


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


def _iter_events(log_dir: str):
    """Yield events from all events-*.jsonl files in chronological order."""
    names = _sorted_event_files(log_dir)
    for name in names:
        path = os.path.join(log_dir, name)
        try:
            # errors="replace" mirrors the reverse reader (_iter_lines_reverse):
            # CJK is written raw (ensure_ascii=False), so a crash that tears a
            # multibyte record at EOF must NOT raise UnicodeDecodeError out of the
            # manual replay path — the torn line is then dropped by the
            # JSONDecodeError guard below, exactly as recover_tail tolerates it.
            with open(path, "r", encoding="utf-8", errors="replace") as f:
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
