import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.core.event_log import EventLog, replay
from app.core.metadata_db import MetadataDB, DB_FILENAME


@pytest.mark.xfail(strict=False, reason="needs Task 6 (MetadataDB emit)")
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


@pytest.mark.xfail(strict=False, reason="needs Task 6 (MetadataDB emit + snapshot event)")
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
