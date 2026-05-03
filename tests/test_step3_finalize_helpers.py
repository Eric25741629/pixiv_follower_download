"""Tests for the helpers extracted from get_img_url_thread._finalize_on_complete."""
from pathlib import Path
import sys
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_url_fetch import get_img_url_thread


from app.core.metadata_db import MetadataDB


def _stub(tmp_path):
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.path = str(tmp_path)
    t.url_meta = {}
    t.url_meta_path = str(tmp_path / "all_url_meta.json")
    t._q = Queue()
    t._metadata_db = MetadataDB(str(tmp_path))
    return t


# ── _split_results_and_errors ────────────────────────────────────────────────

def test_split_results_separates_urls_from_error_pids(tmp_path):
    t = _stub(tmp_path)
    raw = [
        ["https://i.pximg.net/x.jpg", "12345"],   # URL + error PID mixed in same sublist
        ["https://i.pximg.net/y.png"],
        ["67890"],                                 # error-only
        "ignored-because-not-list",
    ]
    urls, errors = t._split_results_and_errors(raw)
    assert urls == ["https://i.pximg.net/x.jpg", "https://i.pximg.net/y.png"]
    assert errors == ["12345", "67890"]


def test_split_results_handles_empty(tmp_path):
    t = _stub(tmp_path)
    urls, errors = t._split_results_and_errors([])
    assert urls == []
    assert errors == []


def test_split_results_handles_no_lists(tmp_path):
    t = _stub(tmp_path)
    urls, errors = t._split_results_and_errors(["string-not-list", None, 42])
    assert urls == []
    assert errors == []


# ── _drain_queue_to_text_file ────────────────────────────────────────────────

def test_drain_queue_appends_items(tmp_path):
    t = _stub(tmp_path)
    q = Queue()
    for v in ["pid1", "pid2", "pid3"]:
        q.put(v)
    t._drain_queue_to_text_file(q, "out.txt", mode_append=True)
    content = (tmp_path / "out.txt").read_text(encoding="utf-8")
    assert "pid1" in content
    assert "pid2" in content
    assert "pid3" in content


def test_drain_queue_empty_writes_nothing_useful(tmp_path):
    t = _stub(tmp_path)
    q = Queue()
    t._drain_queue_to_text_file(q, "empty.txt", mode_append=True)
    # File may or may not exist, but if it does, content is empty
    f = tmp_path / "empty.txt"
    if f.exists():
        assert f.read_text(encoding="utf-8") == ""


def test_drain_queue_overwrite_mode(tmp_path):
    t = _stub(tmp_path)
    # Pre-existing content that should be replaced
    (tmp_path / "out.txt").write_text("STALE", encoding="utf-8")
    q = Queue()
    q.put("fresh1")
    q.put("fresh2")
    t._drain_queue_to_text_file(q, "out.txt", mode_append=False)
    content = (tmp_path / "out.txt").read_text(encoding="utf-8")
    assert "STALE" not in content
    assert "fresh1" in content
    assert "fresh2" in content


# ── _persist_step3_url_meta ──────────────────────────────────────────────────

def test_persist_step3_url_meta_writes_db(tmp_path):
    t = _stub(tmp_path)
    t.url_meta = {"123": {"tag": ["a"], "like": 5}}
    t._persist_step3_url_meta()
    meta = t._metadata_db.get_meta("123")
    assert meta is not None
    assert meta["tag"] == ["a"]
    assert meta["like"] == 5


# ── _persist_step3_net_err ───────────────────────────────────────────────────

def test_persist_net_err_writes_lines(tmp_path):
    t = _stub(tmp_path)
    t._persist_step3_net_err(["err1", "err2", "err3"])
    content = (tmp_path / "net_err.txt").read_text(encoding="utf-8")
    assert "err1" in content
    assert "err2" in content
    assert "err3" in content


def test_persist_net_err_empty_list(tmp_path):
    t = _stub(tmp_path)
    t._persist_step3_net_err([])
    f = tmp_path / "net_err.txt"
    # Allowed to exist as empty
    if f.exists():
        assert f.read_text(encoding="utf-8") == ""


# ── _step3_emit ──────────────────────────────────────────────────────────────

def test_step3_emit_queues_output_event(tmp_path):
    t = _stub(tmp_path)
    t._step3_emit("<p>hi</p>")
    evt = t._q.get_nowait()
    assert evt.type == "output"
    assert evt.data == "<p>hi</p>"
