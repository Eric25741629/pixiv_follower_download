import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.core.event_log import EventLog, replay
from app.core.metadata_db import MetadataDB, DB_FILENAME


def test_replay_roundtrip_from_blank(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    log = EventLog(str(src_dir))
    db = MetadataDB(str(src_dir), event_log=log)

    db.upsert_artworks(["100", "200", "300"])
    db.upsert_artwork("100", page_count=2, like_count=50, tags=["A", "B"],
                      img_url_template="http://x/{p}.jpg",
                      requires_cookie=True,
                      meta_updated_at="2026-05-23 10:00:00")
    db.mark_page_downloaded("100", 0, file_path="/d/100_p0.jpg",
                            file_size=1234, url="http://x/0.jpg")
    db.mark_page_failed("100", 1, failure_reason="404", url="http://x/1.jpg")
    db.mark_artwork_revoked("200", revoked_at="2026-05-23 11:00:00")
    db.close()
    log.close()

    # rebuild into a fresh DB from log alone
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    result = replay(str(dst_dir / DB_FILENAME), str(src_dir / "events"))
    assert result.applied > 0

    db_src = MetadataDB(str(src_dir))
    db_dst = MetadataDB(str(dst_dir))

    def _rows(d):
        c = d._conn()
        a = list(c.execute("SELECT pid, page_count, like_count, tags, "
                           "img_url_template, requires_cookie, meta_updated_at, "
                           "revoked_at FROM artworks ORDER BY pid"))
        p = list(c.execute("SELECT pid, page_index, status, url, file_path, "
                           "file_size, failure_reason FROM pages "
                           "ORDER BY pid, page_index"))
        return a, p

    src_a, src_p = _rows(db_src)
    dst_a, dst_p = _rows(db_dst)
    assert src_a == dst_a
    assert src_p == dst_p


def test_replay_skips_pre_snapshot_events(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    log = EventLog(str(src_dir))
    db = MetadataDB(str(src_dir), event_log=log)

    db.upsert_artwork("1", page_count=1, meta_updated_at="2026-05-23 09:00:00")
    db.mark_page_downloaded("1", 0, url="http://x")

    # snapshot now (mid-log)
    db.backup_db(max_history=10)

    db.upsert_artwork("2", page_count=1, meta_updated_at="2026-05-23 10:00:00")
    db.mark_page_downloaded("2", 0, url="http://y")
    db.close()
    log.close()

    history = src_dir / "history"
    snaps = sorted(history.glob(f"{DB_FILENAME}.*"))
    assert snaps, "snapshot was not produced"
    snapshot_path = str(snaps[-1])

    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    result = replay(str(dst_dir / DB_FILENAME),
                    str(src_dir / "events"),
                    snapshot_path=snapshot_path)
    assert result.applied >= 2
    assert result.skipped_pre_snapshot >= 2

    db_dst = MetadataDB(str(dst_dir))
    assert db_dst.get_artwork("1") is not None
    assert db_dst.get_artwork("2") is not None


def test_replay_keeps_same_millisecond_post_snapshot_event(tmp_path, monkeypatch):
    """A mutation emitted in the SAME wall-clock millisecond as the snapshot
    (but after the backup ran) must survive replay.

    Regression for the millisecond-resolution cutoff: event 't' is only
    millisecond-precise, and the snapshot event is written immediately before
    the next mutation, so the two routinely share a millisecond. The old
    'skip if t <= snapshot_ts' dropped that post-snapshot mutation, silently
    losing data (~6% of real runs). A stepped fake clock pins the snapshot and
    artwork '2' to the same millisecond to make the collision deterministic.
    """
    import app.core.event_log as ev_mod

    clock = {"ms": 1}

    def _stepped_now_iso():
        return f"2026-05-23T09:00:00.{clock['ms']:03d}"

    monkeypatch.setattr(ev_mod, "_now_iso", _stepped_now_iso)

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    log = EventLog(str(src_dir))            # session.start @ .001
    db = MetadataDB(str(src_dir), event_log=log)

    db.upsert_artwork("1", page_count=1, meta_updated_at="2026-05-23 09:00:00")
    db.mark_page_downloaded("1", 0, url="http://x")

    clock["ms"] = 2                         # snapshot AND artwork "2" share .002
    db.backup_db(max_history=10)            # snapshot image holds only "1"
    db.upsert_artwork("2", page_count=1, meta_updated_at="2026-05-23 10:00:00")
    db.mark_page_downloaded("2", 0, url="http://y")

    clock["ms"] = 3
    db.close()
    log.close()

    history = src_dir / "history"
    snaps = sorted(history.glob(f"{DB_FILENAME}.*"))
    assert snaps, "snapshot was not produced"
    snapshot_path = str(snaps[-1])

    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    replay(str(dst_dir / DB_FILENAME),
           str(src_dir / "events"),
           snapshot_path=snapshot_path)

    db_dst = MetadataDB(str(dst_dir))
    # "1" comes from the snapshot image; "2" was added in the snapshot's
    # millisecond AFTER the backup, so it is not in the image and must replay.
    assert db_dst.get_artwork("1") is not None
    assert db_dst.get_artwork("2") is not None, (
        "post-snapshot event sharing the snapshot's millisecond was dropped"
    )
