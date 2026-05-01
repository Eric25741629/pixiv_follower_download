import threading
import time
import queue as _queue
from unittest.mock import patch

import pytest
import requests

from app.core.pixiv_thread_base import (
    PauseableThread,
    NETWORK_RETRY_ATTEMPTS,
    NETWORK_RETRY_WAIT_SEC,
)
from app.core.worker_event import WorkerEvent


class _Worker(PauseableThread):
    def run(self):
        pass


def _make_worker():
    q = _queue.Queue()
    w = _Worker(q)
    return w, q


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except _queue.Empty:
            return out


def test_constants_are_5_and_60():
    assert NETWORK_RETRY_ATTEMPTS == 5
    assert NETWORK_RETRY_WAIT_SEC == 60


def test_first_attempt_succeeds():
    w, q = _make_worker()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    ok, result, exc = w._run_with_network_retry("PID 1", fn)

    assert ok is True
    assert result == "ok"
    assert exc is None
    assert calls["n"] == 1
    events = _drain(q)
    assert all(e.type != "output" or "重試" not in e.data for e in events)


def test_third_attempt_succeeds_after_two_proxy_errors():
    w, q = _make_worker()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ProxyError("dead")
        return "ok"

    with patch.object(PauseableThread, "_wait_interruptible", return_value=True) as wait:
        ok, result, exc = w._run_with_network_retry("PID 9", fn)

    assert ok is True
    assert result == "ok"
    assert calls["n"] == 3
    assert wait.call_count == 2
    wait.assert_called_with(NETWORK_RETRY_WAIT_SEC)
    output_msgs = [e.data for e in _drain(q) if e.type == "output"]
    yellow = [m for m in output_msgs if "#b58900" in m]
    red = [m for m in output_msgs if "color='red'" in m]
    assert len(yellow) == 2
    assert red == []


def test_all_five_attempts_fail():
    w, q = _make_worker()
    calls = {"n": 0}
    err = requests.exceptions.ConnectionError("dead")

    def fn():
        calls["n"] += 1
        raise err

    with patch.object(PauseableThread, "_wait_interruptible", return_value=True):
        ok, result, exc = w._run_with_network_retry("PID 9", fn)

    assert ok is False
    assert result is None
    assert exc is err
    assert calls["n"] == NETWORK_RETRY_ATTEMPTS
    output_msgs = [e.data for e in _drain(q) if e.type == "output"]
    yellow = [m for m in output_msgs if "#b58900" in m]
    red = [m for m in output_msgs if "color='red'" in m]
    assert len(yellow) == NETWORK_RETRY_ATTEMPTS - 1
    assert len(red) == 1
    assert "停用此 Cookie" in red[0]


def test_stop_during_wait_returns_immediately():
    w, q = _make_worker()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise requests.exceptions.ProxyError("dead")

    with patch.object(PauseableThread, "_wait_interruptible", return_value=False):
        ok, result, exc = w._run_with_network_retry("PID 9", fn)

    assert ok is False
    assert result is None
    assert calls["n"] == 1


def test_stop_event_set_before_first_attempt():
    w, q = _make_worker()
    w._stop_event.set()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    ok, result, exc = w._run_with_network_retry("PID 9", fn)
    assert ok is False
    assert result is None
    assert calls["n"] == 0


def test_non_network_exception_propagates():
    w, q = _make_worker()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        w._run_with_network_retry("PID 9", fn)
    assert calls["n"] == 1


def test_wait_interruptible_full_duration_returns_true():
    w, _q = _make_worker()
    # Force Event.wait to return False (no stop) every poll, so the 0.5 s
    # slice loop runs to completion.
    with patch.object(threading.Event, "wait", return_value=False):
        assert w._wait_interruptible(1) is True


def test_wait_interruptible_stop_fires_returns_false():
    w, _q = _make_worker()
    w._stop_event.set()
    assert w._wait_interruptible(60) is False


def test_wait_interruptible_pause_does_not_consume_budget():
    """While paused, _wait_interruptible must block without consuming the
    countdown budget, then complete normally once unpaused."""
    w, _q = _make_worker()
    w._pause_event.clear()  # paused

    started = threading.Event()
    finished = threading.Event()
    result = {}

    def runner():
        started.set()
        result["v"] = w._wait_interruptible(1)
        finished.set()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    started.wait()
    time.sleep(0.6)
    assert not finished.is_set(), "wait should still be blocked while paused"
    w._pause_event.set()
    finished.wait(timeout=3.0)
    assert finished.is_set()
    assert result["v"] is True
