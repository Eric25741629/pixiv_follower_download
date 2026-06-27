"""Closed-artwork-set process-cache primitives.

Extracted from ``metadata_db.py`` (file-size refactor). Holds the
process-global cache dict + lock and the cheap DB file-signature that
invalidates it. ``MetadataDB.closed_artwork_set`` composes these with the
SQL-side computation; isolating them here keeps the caching mechanism in
one place and independently testable.
"""
from __future__ import annotations

import os
import threading

# ── closed-artwork set cache ──────────────────────────────────────────────
# ``closed_artwork_set`` is the single most expensive call in the whole
# startup path: on a real-world 1.26M-row DB it returns ~1.1M PIDs and the
# old ``SELECT pid FROM v_closed_artworks`` spent ~23s building a TEMP
# B-TREE to dedupe the 3-branch UNION. It is called 5-6 times per Run All
# (``_build_step2/3``, ``_build_combined``, the folder-sync DB augment,
# ``download_thread._load_initial_exist_pid_set`` and ``emit_db_stats``'s
# ``downloaded_count``), so the cost multiplies.
#
# This process-global cache keys on a cheap DB file signature — the
# (size, mtime_ns) of ``metadata.sqlite3`` plus its ``-wal`` sidecar. Any
# committed write appends frames to the WAL (or rewrites the main file on
# checkpoint), so a *matching* signature provably means the closed set is
# unchanged. A mismatch (our own writes, another process, the CLI) recomputes
# automatically — there is no manual invalidation to get wrong. Different
# MetadataDB instances pointed at the same file share the cache, so the
# build-time call and the worker-thread call collapse into one compute.
_CLOSED_SET_CACHE: dict[str, tuple] = {}
_CLOSED_SET_CACHE_LOCK = threading.RLock()


def _db_file_signature(db_path: str) -> tuple:
    """Cheap change signature: (size, mtime_ns) of the DB file + its -wal.

    Two ``os.stat`` calls, no row access. ``None`` for a missing file so a
    not-yet-created DB and a freshly-deleted one compare distinctly.
    """
    sig = []
    for suffix in ("", "-wal"):
        try:
            st = os.stat(db_path + suffix)
            sig.append((st.st_size, st.st_mtime_ns))
        except OSError:
            sig.append(None)
    return tuple(sig)
