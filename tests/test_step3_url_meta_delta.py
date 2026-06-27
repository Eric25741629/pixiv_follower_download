"""FIX #1+#2 — Step 3 url_meta delta-flush regression + perf guard.

Before the fix, ``get_img_url_thread.url_meta`` was re-imported in full on
every periodic batch flush (and on every per-GIF cookie-usage stamp), so
mirroring N PIDs in batches of 25 cost sum(25, 50, ..., N) = O(N^2/50) row
imports. The delta guard (``self._flushed_meta_pids``, mirroring the existing
``_flushed_urls`` guard) makes each flush import only the un-mirrored delta,
collapsing the total to O(N). The terminal full-dict flushes remain a backstop;
``import_meta_dict`` is ON CONFLICT DO UPDATE / COALESCE so the end state is
byte-identical.
"""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_url_fetch import get_img_url_thread


class _CountingDB:
    """Fake metadata DB whose import_meta_dict records every row it is asked
    to import, so the test can measure total work and final coverage."""

    def __init__(self):
        self.total_rows_imported = 0          # cumulative across all calls
        self.call_count = 0                   # number of import_meta_dict calls
        self.last_imported_pids = set()       # union of all PIDs ever imported
        self.import_sizes = []                # per-call sub-dict size

    def import_meta_dict(self, meta):
        if not isinstance(meta, dict) or not meta:
            return 0
        n = len(meta)
        self.total_rows_imported += n
        self.call_count += 1
        self.import_sizes.append(n)
        self.last_imported_pids.update(meta.keys())
        return n


def _stub():
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.url_meta = {}
    t._flushed_meta_pids = set()
    t._metadata_db = _CountingDB()
    return t


# ── delta flush keeps total work O(N) ─────────────────────────────────────────

def test_periodic_flush_is_linear_not_quadratic():
    t = _stub()
    db = t._metadata_db

    N = 500
    batch = 25
    next_pid = 0
    while next_pid < N:
        # Simulate one batch of newly-resolved PIDs being added to url_meta.
        for _ in range(batch):
            pid = str(next_pid)
            t.url_meta[pid] = {"img_url": f"https://i.pximg.net/{pid}.jpg",
                               "pagecount": 1, "tag": [], "like": 0}
            next_pid += 1
        # The periodic/batch DB-mirror path (_flush_loop_batch -> this).
        t._flush_url_meta_snapshot()

    # Old behavior would import sum(25, 50, ..., 500) = 25 * (1+2+...+20)
    # = 25 * 210 = 5250 rows. Delta flush imports each PID exactly once = N.
    quadratic_cost = sum(range(batch, N + 1, batch))   # == 5250 for N=500
    assert quadratic_cost > N * 4, "sanity: quadratic cost must dwarf linear"
    assert db.total_rows_imported == N, (
        f"delta flush should import each PID once (N={N}), "
        f"got {db.total_rows_imported}"
    )
    assert db.total_rows_imported < quadratic_cost / 5, (
        f"delta flush ({db.total_rows_imported}) must be far below the old "
        f"quadratic cost ({quadratic_cost})"
    )


# ── end state is complete (every PID mirrored) ────────────────────────────────

def test_every_pid_present_after_all_flushes():
    t = _stub()
    db = t._metadata_db

    N = 200
    batch = 25
    next_pid = 0
    while next_pid < N:
        for _ in range(batch):
            pid = str(next_pid)
            t.url_meta[pid] = {"img_url": f"https://i.pximg.net/{pid}.jpg",
                               "pagecount": 1}
            next_pid += 1
        t._flush_url_meta_snapshot()

    # Terminal full-dict backstop must not raise and must re-cover everything.
    t._flush_url_meta_snapshot(full=True)

    expected = {str(i) for i in range(N)}
    assert db.last_imported_pids == expected
    assert expected.issubset(t._flushed_meta_pids)


# ── repeat flush with no new PIDs is a no-op (no redundant import) ─────────────

def test_repeat_flush_without_new_pids_imports_nothing():
    t = _stub()
    db = t._metadata_db
    for i in range(25):
        t.url_meta[str(i)] = {"img_url": f"https://i.pximg.net/{i}.jpg"}
    t._flush_url_meta_snapshot()
    assert db.total_rows_imported == 25
    calls_before = db.call_count

    # No new PIDs added -> delta is empty -> no import call at all.
    t._flush_url_meta_snapshot()
    t._flush_url_meta_snapshot()
    assert db.total_rows_imported == 25
    assert db.call_count == calls_before  # zero extra import calls


# ── per-GIF path flushes only the single re-stamped PID ───────────────────────

def test_per_gif_path_flushes_single_pid_only():
    t = _stub()
    db = t._metadata_db
    for i in range(100):
        t.url_meta[str(i)] = {"img_url": f"https://i.pximg.net/{i}.jpg"}
    t._flush_url_meta_snapshot()
    assert db.total_rows_imported == 100

    # A GIF PID gets cookie_used re-stamped; per-GIF flush must import just it.
    t.url_meta["42"] = {"img_url": "https://i.pximg.net/42.jpg", "cookie_used": True}
    t._persist_url_meta_with_fallback(pid_key="42")
    assert db.import_sizes[-1] == 1, "per-GIF flush must import exactly one row"
    assert "42" in db.last_imported_pids


# ── terminal full backstop re-covers an already-delta-flushed dict ────────────

def test_full_backstop_reimports_whole_dict():
    t = _stub()
    db = t._metadata_db
    for i in range(50):
        t.url_meta[str(i)] = {"img_url": f"https://i.pximg.net/{i}.jpg"}
    t._flush_url_meta_snapshot()           # delta: imports 50
    assert db.total_rows_imported == 50

    t._flush_url_meta_snapshot(full=True)  # backstop: re-imports all 50
    assert db.import_sizes[-1] == 50
    assert db.last_imported_pids == {str(i) for i in range(50)}
