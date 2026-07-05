"""Tests for the helpers extracted from _jxl_record_outcome and _execute_downloads."""
from pathlib import Path
import sys
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


def _stub(tmp_path):
    t = download_thread.__new__(download_thread)
    t._jxl_ok_count = 0
    t._jxl_fail_count = 0
    t._jxl_src_total_bytes = 0
    t._jxl_dst_total_bytes = 0
    t._stats_collector = None
    t._q = Queue()
    t.jxl_delete_original = False
    return t


# ── _jxl_tally_sizes ────────────────────────────────────────────────────────

def test_jxl_tally_sizes_returns_real_sizes(tmp_path):
    t = _stub(tmp_path)
    src = tmp_path / "src.jpg"
    dst = tmp_path / "dst.jxl"
    src.write_bytes(b"a" * 1000)
    dst.write_bytes(b"b" * 600)
    src_size, dst_size, saved = t._jxl_tally_sizes(str(src), str(dst))
    assert src_size == 1000
    assert dst_size == 600
    assert saved == 400
    # Running totals updated
    assert t._jxl_src_total_bytes == 1000
    assert t._jxl_dst_total_bytes == 600


def test_jxl_tally_sizes_handles_missing_file(tmp_path):
    t = _stub(tmp_path)
    src_size, dst_size, saved = t._jxl_tally_sizes(
        str(tmp_path / "nope.jpg"), str(tmp_path / "also_nope.jxl"),
    )
    assert (src_size, dst_size, saved) == (None, None, None)
    # Totals NOT bumped on failure
    assert t._jxl_src_total_bytes == 0


def test_jxl_tally_sizes_silent_when_running_totals_missing(tmp_path):
    """A test stub without the running-total attrs must not crash."""
    t = download_thread.__new__(download_thread)
    t._stats_collector = None
    src = tmp_path / "src.jpg"
    dst = tmp_path / "dst.jxl"
    src.write_bytes(b"a" * 5)
    dst.write_bytes(b"b" * 3)
    # Must not raise
    src_size, dst_size, saved = t._jxl_tally_sizes(str(src), str(dst))
    assert (src_size, dst_size, saved) == (5, 3, 2)


# ── _jxl_emit_compare_log ───────────────────────────────────────────────────

def test_jxl_emit_compare_log_skips_when_size_missing(tmp_path):
    t = _stub(tmp_path)
    t._jxl_emit_compare_log("any.jpg", None, 100, None)
    # Nothing queued because src_size is None
    assert t._q.empty()


def test_jxl_emit_compare_log_skips_when_src_size_zero(tmp_path):
    t = _stub(tmp_path)
    t._jxl_emit_compare_log("any.jpg", 0, 0, 0)
    assert t._q.empty()


def test_jxl_emit_compare_log_emits_when_sizes_present(tmp_path):
    t = _stub(tmp_path)
    t._format_size_human = lambda n: f"{n}B"
    t._jxl_emit_compare_log("foo.jpg", 1000, 600, 400)
    evt = t._q.get_nowait()
    assert evt.type == "output"
    assert "foo.jpg" in evt.data
    assert "1000B" in evt.data
    assert "600B" in evt.data
    assert "40.00%" in evt.data


# ── _jxl_delete_source_if_configured ────────────────────────────────────────

def test_jxl_delete_source_when_flag_off(tmp_path):
    t = _stub(tmp_path)
    t.jxl_delete_original = False
    src = tmp_path / "keep.jpg"
    src.write_bytes(b"x")
    t._jxl_delete_source_if_configured(str(src))
    assert src.exists()


def test_jxl_delete_source_when_flag_on(tmp_path):
    t = _stub(tmp_path)
    t.jxl_delete_original = True
    src = tmp_path / "delete_me.jpg"
    src.write_bytes(b"x")
    t._jxl_delete_source_if_configured(str(src))
    assert not src.exists()


def test_jxl_delete_source_silent_on_missing_file(tmp_path):
    t = _stub(tmp_path)
    t.jxl_delete_original = True
    # Must not raise
    t._jxl_delete_source_if_configured(str(tmp_path / "nope.jpg"))


# ── _jxl_emit_failure_log ───────────────────────────────────────────────────

def test_jxl_emit_failure_log_increments_count_and_emits(tmp_path):
    t = _stub(tmp_path)
    t._jxl_emit_failure_log("bad.jpg", "cjxl exit 1: timeout")
    assert t._jxl_fail_count == 1
    evt = t._q.get_nowait()
    assert "bad.jpg" in evt.data
    assert "cjxl exit 1: timeout" in evt.data


def test_jxl_emit_failure_log_truncates_long_reason(tmp_path):
    t = _stub(tmp_path)
    long_reason = "x" * 500
    t._jxl_emit_failure_log("bad.jpg", long_reason)
    evt = t._q.get_nowait()
    # Reason was truncated to 117 chars + "..."
    assert "x" * 117 + "..." in evt.data
    # Full 500-char reason did NOT make it through
    assert "x" * 200 not in evt.data


def test_jxl_emit_failure_log_default_message_when_reason_blank(tmp_path):
    t = _stub(tmp_path)
    t._jxl_emit_failure_log("bad.jpg", "")
    evt = t._q.get_nowait()
    assert "cjxl conversion failed" in evt.data


# ── _jxl_record_outcome: mtime preservation ────────────────────────────────

def test_jxl_record_outcome_copies_src_mtime_to_dst(tmp_path):
    """The .jxl must inherit the source's timetag-stamped mtime, not keep the
    conversion-time mtime cjxl gave it (set_file_mtime would otherwise be lost
    for every converted file)."""
    import os
    t = _stub(tmp_path)
    t.jxl_delete_original = True
    src = tmp_path / "img.jpg"
    dst = tmp_path / "img.jxl"
    src.write_bytes(b"a" * 10)
    dst.write_bytes(b"b" * 5)
    stamp = 946684800.0  # 2000-01-01, clearly not "now"
    os.utime(src, (stamp, stamp))
    t._jxl_record_outcome(str(src), str(dst), True, "")
    assert os.path.getmtime(dst) == stamp
    assert not src.exists()  # delete_original still honoured (after the copy)
