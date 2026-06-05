import os, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.metadata_db import MetadataDB


def test_pids_with_pending_pages_returns_distinct_pending_pids():
    with tempfile.TemporaryDirectory() as d:
        db = MetadataDB(d, event_log=None)
        # two pages of PID 111 pending, one page of 222 downloaded, one 333 pending
        db.upsert_page("111", 0, status="pending", url="https://x/111_p0.jpg")
        db.upsert_page("111", 1, status="pending", url="https://x/111_p1.jpg")
        db.upsert_page("222", 0, status="downloaded", url="https://x/222_p0.jpg")
        db.upsert_page("333", 0, status="pending", url="https://x/333_p0.jpg")
        result = set(db.pids_with_pending_pages())
        assert result == {"111", "333"}
        db.close()
