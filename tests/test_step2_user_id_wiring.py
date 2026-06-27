"""Tests that Step 2's PID scan plumbs the artist's user_id all the way to
the SQLite metadata DB so each discovered PID gets its author recorded."""
from pathlib import Path
from queue import Queue
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB
from app.core.thread_pid_scan import get_pixiv_author_imgID_Thread


def _make_thread(tmp_path):
    t = get_pixiv_author_imgID_Thread.__new__(get_pixiv_author_imgID_Thread)
    t.path = str(tmp_path)
    t._q = Queue()
    t._stop_event = threading.Event()
    t._pause_event = threading.Event()
    t._pause_event.set()
    t.exist_pid = set()
    t._init_step2_run_state()
    return t


def test_collected_pids_stores_pid_user_id_tuples(tmp_path):
    """``_step2_append_new_pids`` must store ``(pid, user_id)`` tuples so the
    write phase can later persist the artist to the DB."""
    t = _make_thread(tmp_path)
    t._step2_append_new_pids(["100", "200"], author_id="999")
    assert t._collected_pids == [("100", "999"), ("200", "999")]


def test_collected_pids_back_compat_without_author(tmp_path):
    """No ``author_id`` (e.g. legacy callers) records user_id as None."""
    t = _make_thread(tmp_path)
    t._step2_append_new_pids(["100"])
    assert t._collected_pids == [("100", None)]


def test_write_step2_pictures_id_writes_user_id_to_db(tmp_path):
    """End-to-end: collected (pid, user_id) tuples → pictures_id.txt has PIDs
    only, but the DB has user_id."""
    t = _make_thread(tmp_path)
    t._metadata_db = MetadataDB(str(tmp_path), event_log=None)
    t._step2_append_new_pids(["100", "200"], author_id="999")
    t._write_step2_pictures_id(end=[])

    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    assert set(pics) == {"100", "200"}
    assert t._metadata_db.get_artwork("100")["user_id"] == "999"
    assert t._metadata_db.get_artwork("200")["user_id"] == "999"
    t._metadata_db.close()


def test_write_step2_pictures_id_handles_mixed_authors(tmp_path):
    """Two _step2_append calls with different author_ids end up as the right
    per-PID author in the DB."""
    t = _make_thread(tmp_path)
    t._metadata_db = MetadataDB(str(tmp_path), event_log=None)
    t._step2_append_new_pids(["100"], author_id="111")
    t._step2_append_new_pids(["200"], author_id="222")
    t._write_step2_pictures_id(end=[])

    assert t._metadata_db.get_artwork("100")["user_id"] == "111"
    assert t._metadata_db.get_artwork("200")["user_id"] == "222"
    t._metadata_db.close()


def test_write_step2_prefers_user_id_bearing_entry_over_end_list(tmp_path):
    """When a PID appears both in ``_collected_pids`` (with user_id) and ``end``
    (the flat run-level list, no user_id), the collected version wins."""
    t = _make_thread(tmp_path)
    t._metadata_db = MetadataDB(str(tmp_path), event_log=None)
    t._step2_append_new_pids(["100"], author_id="999")
    # ``end`` is the run-level flat list that overlaps with _collected_pids.
    t._write_step2_pictures_id(end=["100"])

    # Only one row, user_id preserved.
    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    assert pics == ["100"]
    assert t._metadata_db.get_artwork("100")["user_id"] == "999"
    t._metadata_db.close()


def test_write_step2_preserves_end_ordering(tmp_path):
    """``pictures_id.txt`` write order must follow the ``end`` argument's
    order — this is the historical contract that downstream readers
    implicitly rely on. When the same PID appears in both ``_collected_pids``
    (carrying user_id) and ``end`` (the flattened run-level list), the
    collected version's user_id wins for the DB, but ``end``'s position
    wins for the txt file."""
    t = _make_thread(tmp_path)
    t._metadata_db = MetadataDB(str(tmp_path), event_log=None)
    # collected has 100 with user_id; end has 200, 100, 300 in that order.
    t._step2_append_new_pids(["100"], author_id="111")
    t._write_step2_pictures_id(end=["200", "100", "300"])

    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    # end's order preserved, dedup keeps first occurrence of each PID.
    assert pics == ["200", "100", "300"], (
        f"expected end-ordering preserved, got {pics}"
    )
    # user_id for 100 still came from collected.
    assert t._metadata_db.get_artwork("100")["user_id"] == "111"
    # 200 / 300 appeared only in end, so no author info.
    assert t._metadata_db.get_artwork("200")["user_id"] is None
    assert t._metadata_db.get_artwork("300")["user_id"] is None
    t._metadata_db.close()


def test_write_step2_appends_collected_only_pids_after_end(tmp_path):
    """PIDs that appear only in ``_collected_pids`` (not in ``end``) — e.g.
    flushed incrementally before the run finished — get appended after
    end's items, in collected's discovery order."""
    t = _make_thread(tmp_path)
    t._metadata_db = MetadataDB(str(tmp_path), event_log=None)
    t._step2_append_new_pids(["100", "200"], author_id="111")  # collected only
    t._write_step2_pictures_id(end=["999"])

    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    assert pics == ["999", "100", "200"]
    assert t._metadata_db.get_artwork("100")["user_id"] == "111"
    assert t._metadata_db.get_artwork("200")["user_id"] == "111"
    t._metadata_db.close()


def test_pictures_id_format_is_pid_only(tmp_path):
    """``pictures_id.txt`` must stay one-PID-per-line — downstream readers depend
    on this. ``user_id`` lives in the DB, not the txt file."""
    t = _make_thread(tmp_path)
    t._metadata_db = MetadataDB(str(tmp_path), event_log=None)
    t._step2_append_new_pids(["100", "200", "300"], author_id="999")
    t._write_step2_pictures_id(end=[])

    pics = (tmp_path / "pictures_id.txt").read_text(encoding="utf-8").splitlines()
    for line in pics:
        assert "," not in line and "\t" not in line and line.isdigit()
    t._metadata_db.close()
