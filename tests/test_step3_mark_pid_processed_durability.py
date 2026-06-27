"""Regression tests for the Step-3 per-PID durability fixes (B3 / B6).

B3: a resolved PID must persist ``meta_updated_at`` together with
``page_count`` and its ``pages`` rows, so a crash can never strand it
(meta marked done but no page rows -> invisible to every view).

B6: an empty/transient-payload PID (the ``[str(pid)]`` error sentinel) must
stay PENDING for retry, not be silently stamped done.
"""
import threading
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB
from app.core.thread_url_fetch import get_img_url_thread


def _stub(tmp_path):
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.url_meta = {}
    t._metadata_db = MetadataDB(str(tmp_path))
    t._pending_pid_remaining = set()
    t._pending_pid_lock = threading.Lock()
    t._revoked_pid_set = set()
    return t


def test_resolved_pid_persists_meta_and_pages_together(tmp_path):
    """B3: marking a resolved PID processed must leave meta_updated_at AND
    page_count AND pages rows all committed — never meta-done-without-pages."""
    t = _stub(tmp_path)
    pid = "123456"
    t._pending_pid_remaining.add(pid)
    t.url_meta[pid] = {
        "tag": ["x"], "like": 200, "pagecount": 2,
        "img_url": "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/123456_p.jpg",
        "requires_cookie": False,
    }
    # Resolved result: a list of http page URLs.
    t._mark_pid_processed(pid, [
        "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/123456_p0.jpg",
        "https://i.pximg.net/img-original/img/2024/01/01/12/00/00/123456_p1.jpg",
    ])

    meta = t._metadata_db.get_meta(pid)
    assert meta is not None, "meta_updated_at must be set for a resolved PID"
    assert meta["pagecount"] == 2, "page_count must land WITH meta_updated_at (no stranding)"
    assert t._metadata_db.pending_url_count() == 2, "page rows must be seeded"
    assert pid not in t._pending_pid_remaining, "resolved PID drops from the tracker"


def test_transient_payload_pid_stays_pending(tmp_path):
    """B6: an empty/transient-payload PID (error sentinel) stays pending and is
    NOT stamped done, so it is re-queried next run."""
    t = _stub(tmp_path)
    pid = "777777"
    t._pending_pid_remaining.add(pid)
    # No url_meta entry (resolve returned None -> get_download_url emits [pid]).
    t._mark_pid_processed(pid, [pid])

    assert t._metadata_db.has_meta(pid) is False, "transient PID must not be marked done"
    assert pid in t._pending_pid_remaining, "transient PID must stay pending for retry"


def test_filtered_pid_is_marked_done(tmp_path):
    """A filtered-out / exist_pid-skip PID (empty result) is processed-with-
    nothing-to-download: it drops from the tracker and is marked done so it is
    not re-queried forever."""
    t = _stub(tmp_path)
    pid = "888888"
    t._pending_pid_remaining.add(pid)
    t._metadata_db.upsert_artworks([pid])  # discovered row exists (Step 2)
    t._mark_pid_processed(pid, [])

    assert pid not in t._pending_pid_remaining
    assert t._metadata_db.has_meta(pid) is True  # meta_updated_at stamped (no pages)
