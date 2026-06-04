import os, sys, tempfile, datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from queue import Queue
from app.core.thread_combined import combined_thread


def test_work_queue_unions_pictures_id_and_db_pending():
    path = tempfile.mkdtemp()
    # pictures_id.txt has 100 (needs query); DB has pending 200 + already-in 100
    with open(os.path.join(path, "pictures_id.txt"), "w", encoding="utf-8") as f:
        f.write("100\n")
    t = combined_thread(
        q=Queue(), Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=path,
        single_thread_mode=True, download_path=path,
        download_time=datetime.datetime(1970, 1, 1),
    )
    db = t.fetcher._metadata_db
    db.upsert_page("200", 0, status="pending", url="https://x/200_p0.jpg")
    db.upsert_page("100", 0, status="pending", url="https://x/100_p0.jpg")

    query_pids, download_only = t._build_work_lists()
    assert "100" in query_pids
    assert "200" in download_only
    assert "100" not in download_only  # already covered by query path
