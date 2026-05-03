"""Tests for read_json_with_recovery — auto-restoring from history/
when the primary JSON file is corrupt."""
import json
import os
import time
from app.core.pixiv_thread_utils import read_json_with_recovery


def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_returns_missing_when_file_absent(tmp_path):
    path = tmp_path / "all_url_meta.json"
    value, status = read_json_with_recovery(str(path), default={"x": 1})
    assert status == "missing"
    assert value == {"x": 1}


def test_returns_ok_for_valid_file(tmp_path):
    path = tmp_path / "all_url_meta.json"
    _write(str(path), json.dumps({"a": 1, "b": 2}))
    value, status = read_json_with_recovery(str(path), default={})
    assert status == "ok"
    assert value == {"a": 1, "b": 2}


def test_recovers_from_history_when_corrupt(tmp_path):
    path = tmp_path / "all_url_meta.json"
    _write(str(path), '{"a": 1, "incomplete')  # corrupt
    hist = tmp_path / "history"
    hist.mkdir()
    backup = hist / "all_url_meta.json.20260501"
    _write(str(backup), json.dumps({"recovered": True, "n": 42}))
    # Make sure backup mtime is in the past so "newest" sort works.
    os.utime(str(backup), (time.time() - 10, time.time() - 10))

    captured = []
    value, status = read_json_with_recovery(
        str(path), default={}, emit=lambda html: captured.append(html),
    )
    assert status == "recovered"
    assert value == {"recovered": True, "n": 42}
    # Restored file should be valid JSON now.
    with open(str(path), encoding="utf-8") as f:
        assert json.load(f) == {"recovered": True, "n": 42}
    # User got a "解析失敗" + "[還原]" notification.
    joined = "".join(captured)
    assert "解析失敗" in joined
    assert "[還原]" in joined


def test_returns_corrupt_when_no_backups(tmp_path):
    path = tmp_path / "all_url_meta.json"
    _write(str(path), '{"a":')  # corrupt
    captured = []
    value, status = read_json_with_recovery(
        str(path), default={}, emit=lambda html: captured.append(html),
    )
    assert status == "corrupt"
    assert value == {}
    assert any("無 history/" in c for c in captured)


def test_picks_newest_backup_when_multiple_exist(tmp_path):
    path = tmp_path / "all_url_meta.json"
    _write(str(path), "garbage")
    hist = tmp_path / "history"
    hist.mkdir()
    older = hist / "all_url_meta.json.old"
    _write(str(older), json.dumps({"v": "old"}))
    newer = hist / "all_url_meta.json.new"
    _write(str(newer), json.dumps({"v": "new"}))
    os.utime(str(older), (time.time() - 100, time.time() - 100))
    os.utime(str(newer), (time.time() - 1, time.time() - 1))

    value, status = read_json_with_recovery(str(path), default={})
    assert status == "recovered"
    assert value == {"v": "new"}


def test_recovered_json_null_is_distinguished_from_no_backup(tmp_path):
    """Edge case: a backup containing literal `null` must be returned as
    a recovered value, not confused with 'no backup found'."""
    path = tmp_path / "all_url_meta.json"
    _write(str(path), "garbage")
    hist = tmp_path / "history"
    hist.mkdir()
    bk = hist / "all_url_meta.json.null_backup"
    _write(str(bk), "null")
    value, status = read_json_with_recovery(str(path), default={"sentinel": 1})
    assert status == "recovered"
    assert value is None  # legitimately recovered None, not the default


def test_skips_corrupt_backup_falls_through_to_next(tmp_path):
    path = tmp_path / "all_url_meta.json"
    _write(str(path), "garbage")
    hist = tmp_path / "history"
    hist.mkdir()
    bad = hist / "all_url_meta.json.bad"
    _write(str(bad), '{"oops')
    good = hist / "all_url_meta.json.good"
    _write(str(good), json.dumps({"v": "good"}))
    # Make corrupt backup newer so it's tried first; we expect fallthrough.
    os.utime(str(good), (time.time() - 100, time.time() - 100))
    os.utime(str(bad), (time.time() - 1, time.time() - 1))

    value, status = read_json_with_recovery(str(path), default={})
    assert status == "recovered"
    assert value == {"v": "good"}
