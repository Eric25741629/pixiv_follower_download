"""Tests for the parallel JXL conversion worker pool in ``_JXLMixin``.

The pool drains one shared FIFO queue with several daemon workers.  These
tests build a minimal stub of ``download_thread`` (mixin-only state) and
monkeypatch ``_convert_file_to_jxl`` with a fake so no real ``cjxl.exe`` runs.
"""
from pathlib import Path
import sys
import threading
from queue import Empty, Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


def _stub(workers=4):
    """Build a JXL-only stub with the state ``_JXLMixin`` touches."""
    t = download_thread.__new__(download_thread)
    t.jxl_enable = True
    t._stop_event = threading.Event()
    t._jxl_queue = Queue()
    t._jxl_workers = workers
    t._jxl_worker_threads = []
    t._jxl_stats_lock = threading.Lock()
    t._jxl_ok_count = 0
    t._jxl_fail_count = 0
    t._jxl_src_total_bytes = 0
    t._jxl_dst_total_bytes = 0
    return t


# ── 1. Parallelism ──────────────────────────────────────────────────────────

def test_workers_convert_in_parallel():
    """At least two conversions run concurrently (a Barrier of 2 must pass)."""
    t = _stub(workers=4)
    barrier = threading.Barrier(2, timeout=5)
    passed = []

    def fake_convert(src_path):
        # Both files must be in-flight at the same time for this to return;
        # a single sequential worker would deadlock and hit the timeout.
        barrier.wait()
        passed.append(src_path)

    t._convert_file_to_jxl = fake_convert
    for i in range(2):
        t._enqueue_jxl(f"file_{i}.jpg")
    t._drain_jxl_queue()

    assert sorted(passed) == ["file_0.jpg", "file_1.jpg"]


# ── 2. Drain correctness ────────────────────────────────────────────────────

def test_drain_processes_all_and_workers_exit():
    t = _stub(workers=4)
    processed = []
    lock = threading.Lock()

    def fake_convert(src_path):
        with lock:
            processed.append(src_path)

    t._convert_file_to_jxl = fake_convert
    for i in range(20):
        t._enqueue_jxl(f"file_{i}.jpg")
    t._drain_jxl_queue()

    assert len(processed) == 20
    assert sorted(processed) == sorted(f"file_{i}.jpg" for i in range(20))
    # Every spawned worker has exited.
    assert all(not w.is_alive() for w in t._jxl_worker_threads)


# ── 3. Stop still converts the full backlog ─────────────────────────────────

def test_stop_converts_full_backlog_and_updates_spinner():
    """After stop, drain converts EVERYTHING (no discard) and updates the
    stop spinner text with the remaining count via 'loading' events."""
    t = _stub(workers=2)
    t._q = Queue()
    calls = []
    lock = threading.Lock()
    release = threading.Event()

    def fake_convert(src_path):
        # Hold conversions so the drain loop observes a non-empty backlog
        # (and emits at least one spinner update) before letting them finish.
        release.wait(timeout=5)
        with lock:
            calls.append(src_path)

    t._convert_file_to_jxl = fake_convert
    for i in range(50):
        t._enqueue_jxl(f"file_{i}.jpg")

    t._stop_event.set()
    threading.Timer(1.0, release.set).start()
    t._drain_jxl_queue()

    # Nothing dropped: every enqueued file was converted despite the stop.
    assert len(calls) == 50
    events = []
    while True:
        try:
            events.append(t._q.get_nowait())
        except Empty:
            break
    loading = [ev for ev in events if ev.type == "loading"]
    assert loading, "stop drain should update the spinner text"
    busy, msg = loading[0].data
    assert busy is True
    assert "JXL" in msg


# ── 4. Counters aggregate correctly across workers ──────────────────────────

def test_counters_aggregate_across_workers():
    t = _stub(workers=4)

    def fake_convert(src_path):
        # Bump a shared counter through the same lock the real code uses.
        with t._jxl_stats_guard():
            t._jxl_ok_count += 1

    t._convert_file_to_jxl = fake_convert
    for i in range(100):
        t._enqueue_jxl(f"file_{i}.jpg")
    t._drain_jxl_queue()

    assert t._jxl_ok_count == 100
