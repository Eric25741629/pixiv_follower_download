import os, sys, tempfile, datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from queue import Queue
from app.core.thread_combined import combined_thread


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


def test_run_emits_terminal_next_minus_one():
    path = tempfile.mkdtemp()
    open(os.path.join(path, "pictures_id.txt"), "w").close()  # empty -> no work
    q = Queue()
    t = combined_thread(
        q=q, Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=path,
        single_thread_mode=True, download_path=path,
        download_time=datetime.datetime(1970, 1, 1),
    )
    t.run()
    events = _drain(q)
    next_vals = [e.data for e in events if getattr(e, "type", None) == "next"]
    assert next_vals and next_vals[-1] == -1
