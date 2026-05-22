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
    ticks = [json.loads(ln) for ln in lines if json.loads(ln)["k"] == "test.tick"]
    assert len(ticks) == n_threads * n_per_thread
    for ln in lines:
        json.loads(ln)
