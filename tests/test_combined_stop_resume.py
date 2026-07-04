"""邊查邊下: stopping mid-PID must NOT mark the PID fully done.

`_download_pid_group` only reports *attempted* failures, so when the user hits
停止 after page 5/8 the un-attempted pages 6-8 are simply absent from the
returned `failed` list (it is `[]`). The old code read that empty list as
"every page succeeded" and marked the whole PID downloaded + closed +
processed, so the next run skipped the PID entirely and pages 6-8 were lost.
The fix: when `_stop_event` is set after the download leg, treat the PID as
incomplete so its seeded pending pages + pictures_id.txt entry survive for the
next run to resume.
"""
import datetime
import os
import sys
import tempfile
from queue import Queue

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.thread_combined import combined_thread


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


class _Acc:
    cookie = "c1"
    proxy_url = None


def _thread():
    path = tempfile.mkdtemp()
    q = Queue()
    t = combined_thread(
        q=q,
        Author_list=[],
        Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}],
        exist_pid=set(),
        ban_tag=[],
        must_tag=[],
        like_num=0,
        no_to_check=[],
        base_path=path,
        single_thread_mode=True,
        download_path=path,
        download_time=datetime.datetime(1970, 1, 1),
    )
    return t, q


def test_stop_mid_pid_keeps_pid_pending_for_resume():
    t, _q = _thread()
    urls = [f"https://i.pximg.net/img/55501_p{i}.jpg" for i in range(8)]

    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: None
    t._seed_pending_urls = lambda pid, u: None
    t._persist_pid_meta = lambda pid: None

    marked_done = []
    t._mark_urls_done = lambda u: marked_done.append(list(u))
    flushed = []
    t.downloader._maybe_flush_exist_pid = lambda pid: flushed.append(pid)

    def fake_retry(label, fn):
        if "下載" in label:
            t._stop_event.set()        # user pressed 停止 mid-download
            return True, [], None      # only attempted-and-failed reported -> []
        return True, urls, None        # query resolved 8 page urls

    t._run_with_network_retry = fake_retry

    t._process_one_pid("55501", needs_query=True)

    # Incomplete: nothing flipped to downloaded, PID not closed, and the
    # genuine-success flag stays False so run() won't _mark_pid_processed it.
    assert marked_done == [], "stopped PID must not be marked fully downloaded"
    assert flushed == [], "stopped PID must not be flushed to the closed set"
    assert t._last_pid_ok is False


def test_clean_full_download_still_marks_done():
    """Regression guard: a genuine clean finish (no stop) still completes."""
    t, _q = _thread()
    urls = [f"https://i.pximg.net/img/777_p{i}.jpg" for i in range(3)]

    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True, work_units=1, pages=0: None
    t._seed_pending_urls = lambda pid, u: None
    t._persist_pid_meta = lambda pid: None

    marked_done = []
    t._mark_urls_done = lambda u: marked_done.append(list(u))
    flushed = []
    t.downloader._maybe_flush_exist_pid = lambda pid: flushed.append(pid)

    def fake_retry(label, fn):
        if "下載" in label:
            return True, [], None      # no stop, no failures -> clean finish
        return True, urls, None

    t._run_with_network_retry = fake_retry

    t._process_one_pid("777", needs_query=True)

    assert marked_done == [urls]
    assert flushed == ["777"]
    assert t._last_pid_ok is True
