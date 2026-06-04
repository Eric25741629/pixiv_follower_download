import os, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from queue import Queue
from app.core.thread_combined import combined_thread


def _make():
    q = Queue()
    path = tempfile.mkdtemp()
    t = combined_thread(
        q=q,
        Author_list=[],
        Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}],
        exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[],
        base_path=path,
        single_thread_mode=True,
        download_path=path,
        download_time=__import__("datetime").datetime(1970, 1, 1),
    )
    return t


def test_combined_shares_events_and_db_between_engines():
    t = _make()
    assert t.fetcher._pause_event is t._pause_event
    assert t.fetcher._stop_event is t._stop_event
    assert t.downloader._pause_event is t._pause_event
    assert t.downloader._stop_event is t._stop_event
    assert t.downloader._metadata_db is t.fetcher._metadata_db
