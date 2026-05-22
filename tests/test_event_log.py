import datetime
import json
import os
import threading
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.core.event_log import EventLog


def test_emit_writes_single_jsonl_line(tmp_path):
    log = EventLog(str(tmp_path))
    log.emit("page.downloaded", pid="123", page_index=0, url="http://x", file_size=10)
    log.close()

    files = sorted(os.listdir(tmp_path / "events"))
    assert len(files) == 1
    lines = (tmp_path / "events" / files[0]).read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(ln) for ln in lines]
    downloaded = [p for p in payloads if p["k"] == "page.downloaded"]
    assert len(downloaded) == 1
    e = downloaded[0]
    assert e["pid"] == "123"
    assert e["page_index"] == 0
    assert e["url"] == "http://x"
    assert e["file_size"] == 10
    assert "t" in e


def test_emit_is_thread_safe(tmp_path):
    log = EventLog(str(tmp_path))
    n_threads, n_per_thread = 20, 50

    def worker(tid):
        for i in range(n_per_thread):
            log.emit("test.tick", thread=tid, i=i)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    log.close()

    files = sorted(os.listdir(tmp_path / "events"))
    lines = (tmp_path / "events" / files[0]).read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(ln) for ln in lines]
    ticks = [p for p in payloads if p["k"] == "test.tick"]
    assert len(ticks) == n_threads * n_per_thread


def test_rotates_on_date_change(tmp_path, monkeypatch):
    fake_now = [datetime.datetime(2026, 5, 23, 10, 0, 0)]

    class _FakeDT:
        @classmethod
        def now(cls):
            return fake_now[0]

    monkeypatch.setattr("app.core.event_log.datetime", type("M", (), {"datetime": _FakeDT}))

    log = EventLog(str(tmp_path))
    log.emit("test.before_midnight", n=1)
    # advance one day
    fake_now[0] = datetime.datetime(2026, 5, 24, 10, 0, 0)
    log.emit("test.after_midnight", n=2)
    log.close()

    files = sorted(os.listdir(tmp_path / "events"))
    assert files == ["events-20260523.jsonl", "events-20260524.jsonl"]


def test_retention_gc_removes_old_files(tmp_path):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    # create 3 fake old files
    old_paths = []
    for d in (10, 30, 80):
        p = events_dir / f"events-{(datetime.datetime.now() - datetime.timedelta(days=d)).strftime('%Y%m%d')}.jsonl"
        p.write_text("{}\n", encoding="utf-8")
        # backdate mtime
        ts = (datetime.datetime.now() - datetime.timedelta(days=d)).timestamp()
        os.utime(p, (ts, ts))
        old_paths.append((d, p))

    log = EventLog(str(tmp_path), retention_days=60)
    log.emit("test.tick", n=1)  # triggers GC
    log.close()

    remaining = set(os.listdir(events_dir))
    # files <= 60 days old kept; 80-day-old file dropped
    for d, p in old_paths:
        if d <= 60:
            assert p.name in remaining, f"day-{d} file should be kept"
        else:
            assert p.name not in remaining, f"day-{d} file should be GC'd"
