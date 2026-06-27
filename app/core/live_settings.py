"""Signature-memoized live reader for settings.json.

Workers snapshot most settings at construction. To let a mid-run 「儲存設定」
take effect on the next item without restarting the task, the in-scope workers
hold a shared :class:`LiveSettings` and re-pull the relevant sections only when
``settings.json`` actually changed on disk.

The change check is a cheap ``os.stat`` (size + mtime_ns) — the same trick the
metadata DB uses for ``closed_artwork_set``. Until the signature changes,
:meth:`sections` returns the same cached, immutable dict (no JSON re-parse).

This generalizes the only pre-existing live read in the codebase: the
AccountScheduler ``get_cooldown_avg`` lambda, which re-reads
``performance.pid_cooldown_avg`` on every ``release()``.
"""
from __future__ import annotations

import os
import threading

from app.core.settings_store import SettingsStore

# Only the sections that drive per-iteration decisions are exposed live; auth /
# event_log / schedule / ui have their own lifecycles and are never live-applied.
LIVE_SECTIONS = ("download", "filter", "directory", "performance", "jxl")


class LiveSettings:
    """Cheap, signature-memoized reader of the live-relevant settings sections."""

    def __init__(self, base_path: str):
        self._base_path = str(base_path or "")
        self._store = SettingsStore(self._base_path)
        self._settings_file = os.path.join(self._base_path, SettingsStore.FILENAME)
        self._lock = threading.Lock()
        self._sig: tuple[int, int] | None = None
        self._cached: dict | None = None

    def signature(self) -> tuple[int, int] | None:
        """``(size, mtime_ns)`` of settings.json, or None if it doesn't exist.

        O(1) — no file read. A successful ``save()`` rewrites the file (atomic
        rename), so size and/or mtime_ns move and the signature changes.
        """
        try:
            st = os.stat(self._settings_file)
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

    def sections(self) -> dict:
        """Return the live sections, re-reading only when the file changed.

        Returns a fresh dict on reload (rebind, never in-place mutation) so any
        previously-returned reference — e.g. a captured baseline snapshot —
        keeps its old values.
        """
        sig = self.signature()
        with self._lock:
            if self._cached is not None and sig == self._sig:
                return self._cached
            data = self._store.load()
            self._cached = {
                key: dict(data.get(key, {}) or {}) for key in LIVE_SECTIONS
            }
            self._sig = sig
            return self._cached
