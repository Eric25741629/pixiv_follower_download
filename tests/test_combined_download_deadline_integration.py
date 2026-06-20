"""Integration: a deadline/stop inside the real download chain must settle
correctly (2026-06-21 hang fix), exercising
_download_pid_group -> gif_or_jpg -> _dispatch_download -> jpg_download with
only the lowest-level _jpg_attempt stubbed.

Critical (adversary finding #1): a DownloadDeadlineExceeded must be CONTAINED as
a fail-list, never escape as an exception — otherwise combined mode's
_run_with_network_retry (which only catches the network triple) would let it
propagate to run()'s top-level handler and emit 邊查邊下失敗 + next,-1, killing
the whole run on a single trickling image.
"""
import os
import sys
import tempfile
import datetime
from queue import Queue

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.thread_combined import combined_thread
from app.core.pixiv_thread_base import DownloadDeadlineExceeded, DownloadStopped


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def _downloader():
    path = tempfile.mkdtemp()
    t = combined_thread(
        q=Queue(), Author_list=[], Agent="UA",
        cookies=[{"cookie": "c1", "alias": "A"}], exist_pid=set(),
        ban_tag=[], must_tag=[], like_num=0, no_to_check=[], base_path=path,
        single_thread_mode=True, download_path=path,
        download_time=datetime.datetime(1970, 1, 1),
    )
    return t.downloader


def test_deadline_inside_pid_group_returns_faillist_not_raise():
    d = _downloader()
    url = "https://i.pximg.net/img/55501_p0.jpg"

    def boom(u, session, timetag):
        raise DownloadDeadlineExceeded("wedged")

    d._jpg_attempt = boom

    # Must NOT raise; must surface as a non-empty fail-list carrying the url.
    failed = d._download_pid_group("55501", [url])
    assert isinstance(failed, list) and failed, "deadline must surface as a fail-list"
    assert failed[0][0] == url


def test_deadline_default_budget_is_configured():
    """The downloader carries a positive per-page deadline so _stream_to_sink
    always has a budget (the wiring that makes the fix active in production)."""
    d = _downloader()
    assert getattr(d, "_download_deadline_sec", 0) > 0


def test_stop_inside_pid_group_leaves_pending_not_failed():
    d = _downloader()
    if not hasattr(d, "q"):
        d.q = Queue()
    url = "https://i.pximg.net/img/55502_p0.jpg"

    def stopped(u, session, timetag):
        raise DownloadStopped()

    d._jpg_attempt = stopped

    failed = d._download_pid_group("55502", [url])
    assert failed == [], "a user Stop must NOT be recorded as a page failure"
    assert not d.q.empty(), "the stopped url must be handed back to retry next run"
