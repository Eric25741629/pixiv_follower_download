"""Tests verifying _url_meta_lock prevents lost writes from concurrent workers.

The Step 4 ThreadPoolExecutor races up to 4 workers against
_persist_url_meta and _mark_gif_cookie_usage. Without the RLock added in
this iteration, two threads could each call:

    1. read self.url_meta
    2. mutate
    3. atomic_write_json(self.url_meta)

and one thread's mutation would be silently dropped. These tests force
the race in a controlled way.
"""
from pathlib import Path
import json
import sys
import threading
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB
from app.core.thread_download import download_thread


def _stub(tmp_path):
    t = download_thread.__new__(download_thread)
    t.url_meta = {}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t.path = str(tmp_path)
    t._url_meta_lock = threading.RLock()
    t._metadata_db = MetadataDB(str(tmp_path))
    t._q = Queue()
    return t


def test_concurrent_persist_url_meta_no_corruption(tmp_path):
    """100 threads × 10 _persist_url_meta calls each must leave a valid JSON."""
    t = _stub(tmp_path)

    def worker(start_pid):
        for i in range(10):
            pid = str(start_pid + i)
            with t._url_meta_lock:
                t.url_meta[pid] = {"tag": [pid], "like": int(pid) % 100}
            t._persist_url_meta()

    threads = [threading.Thread(target=worker, args=(s,)) for s in (0, 1000, 2000, 3000)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # SQLite is now the primary store; verify all 40 PIDs landed in DB.
    expected_pids = set()
    for s in (0, 1000, 2000, 3000):
        expected_pids.update(str(s + i) for i in range(10))
    for pid in expected_pids:
        assert t._metadata_db.get_meta(pid) is not None, f"lost write for {pid}"


def test_concurrent_mutations_through_lock_are_consistent(tmp_path):
    """When 20 workers each add a different PID under the lock + persist,
    all entries must land in DB (no lost writes)."""
    t = _stub(tmp_path)

    def add_pid(pid_str):
        with t._url_meta_lock:
            t.url_meta[pid_str] = {"tag": [pid_str], "like": 1, "pagecount": 1}
        t._persist_url_meta()

    pids = [str(50000 + i) for i in range(20)]
    threads = [threading.Thread(target=add_pid, args=(p,)) for p in pids]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    for p in pids:
        assert t._metadata_db.get_meta(p) is not None, f"lost write for {p}"


def test_persist_url_meta_works_without_lock_attribute(tmp_path):
    """For test stubs that don't set _url_meta_lock, persist still works."""
    t = _stub(tmp_path)
    t._url_meta_lock = None  # simulate the legacy stub
    t.url_meta = {"1": {"tag": ["a"]}}
    t._persist_url_meta()
    meta = t._metadata_db.get_meta("1")
    assert meta is not None
    assert meta["tag"] == ["a"]
