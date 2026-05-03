"""Tests for exist_pid I/O: load, migration, trash, and write-back (Phase 16)."""
from pathlib import Path
import sys
import json
import os
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pixiv_thread_utils import load_exist_pid_set, trash_file


# ── trash_file ───────────────────────────────────────────────────────────────

def test_trash_file_moves_to_trash_dir(tmp_path):
    f = tmp_path / "old.json"
    f.write_text('[]', encoding='utf-8')
    trash_file(str(f), str(tmp_path))
    assert not f.exists()
    trash_dir = tmp_path / "trash"
    assert trash_dir.exists()
    trashed = list(trash_dir.iterdir())
    assert len(trashed) == 1
    assert "old.json" in trashed[0].name


def test_trash_file_missing_source_is_noop(tmp_path):
    trash_file(str(tmp_path / "nonexistent.json"), str(tmp_path))
    # Trash dir may be created (for cleanup), but no file should be inside
    trash_dir = tmp_path / "trash"
    if trash_dir.exists():
        assert len(list(trash_dir.iterdir())) == 0


def test_trash_file_cleans_old_entries(tmp_path):
    trash_dir = tmp_path / "trash"
    trash_dir.mkdir()
    old_file = trash_dir / "20000101_000000_old.json"
    old_file.write_text("{}", encoding='utf-8')
    # Set mtime to 40 days ago
    old_ts = time.time() - 40 * 86400
    os.utime(str(old_file), (old_ts, old_ts))

    f = tmp_path / "new.json"
    f.write_text('[]', encoding='utf-8')
    trash_file(str(f), str(tmp_path), max_days=30)

    # Old file should be deleted, new file should be trashed
    remaining = list(trash_dir.iterdir())
    assert not any("old.json" in r.name for r in remaining)
    assert any("new.json" in r.name for r in remaining)


# ── load_exist_pid_set ───────────────────────────────────────────────────────

def test_load_from_primary_json(tmp_path):
    data = ["111", "222p1", "333p0"]
    (tmp_path / "exist_pid.json").write_text(json.dumps(data), encoding='utf-8')
    s = load_exist_pid_set(str(tmp_path))
    assert "111" in s
    assert "222p1" in s   # p1 preserved (page tracking)
    assert "333" in s     # p0 stripped to pure PID


def test_load_from_primary_json_trashes_legacy(tmp_path):
    (tmp_path / "exist_pid.json").write_text('["111"]', encoding='utf-8')
    (tmp_path / "exist.json").write_text('["222"]', encoding='utf-8')
    (tmp_path / "existPID.txt").write_text("333\n", encoding='utf-8')
    load_exist_pid_set(str(tmp_path))
    assert not (tmp_path / "exist.json").exists()
    assert not (tmp_path / "existPID.txt").exists()
    assert (tmp_path / "trash").exists()


def test_migrate_from_legacy_json(tmp_path):
    data = ["444", "555p2"]
    (tmp_path / "exist.json").write_text(json.dumps(data), encoding='utf-8')
    s = load_exist_pid_set(str(tmp_path))
    assert "444" in s
    assert "555p2" in s
    # New primary file created
    assert (tmp_path / "exist_pid.json").exists()
    # Old file trashed
    assert not (tmp_path / "exist.json").exists()


def test_migrate_from_legacy_txt(tmp_path):
    (tmp_path / "existPID.txt").write_text("666\n777p1\n", encoding='utf-8')
    s = load_exist_pid_set(str(tmp_path))
    assert "666" in s
    assert "777p1" in s
    assert (tmp_path / "exist_pid.json").exists()
    assert not (tmp_path / "existPID.txt").exists()


def test_load_missing_all_returns_empty(tmp_path):
    s = load_exist_pid_set(str(tmp_path))
    assert s == set()


def test_load_corrupt_json_returns_empty(tmp_path):
    (tmp_path / "exist_pid.json").write_text("not json", encoding='utf-8')
    s = load_exist_pid_set(str(tmp_path))
    assert s == set()


def test_load_empty_base_path():
    s = load_exist_pid_set("")
    assert s == set()


# ── page tracking: p0 strip only, p1/p2 preserved ───────────────────────────

def test_p0_stripped_to_pure_pid(tmp_path):
    data = ["123456p0"]
    (tmp_path / "exist_pid.json").write_text(json.dumps(data), encoding='utf-8')
    s = load_exist_pid_set(str(tmp_path))
    assert "123456" in s
    assert "123456p0" not in s


def test_p1_p2_preserved(tmp_path):
    data = ["123456p1", "123456p2"]
    (tmp_path / "exist_pid.json").write_text(json.dumps(data), encoding='utf-8')
    s = load_exist_pid_set(str(tmp_path))
    assert "123456p1" in s
    assert "123456p2" in s


# ── DB augmentation: union with metadata.sqlite3 downloaded set ─────────────

def test_load_unions_with_metadata_db(tmp_path):
    """A PID present in the DB but absent from JSON must still be returned —
    covers the case of a previous worker crashing after marking the DB but
    before flushing the JSON file."""
    from app.core.metadata_db import MetadataDB

    (tmp_path / "exist_pid.json").write_text('["100"]', encoding='utf-8')
    db = MetadataDB(str(tmp_path))
    db.mark_downloaded("200")
    db.mark_downloaded("300")
    db.close()

    s = load_exist_pid_set(str(tmp_path))
    assert s == {"100", "200", "300"}


def test_load_with_db_only_returns_db_set(tmp_path):
    """When the JSON file is missing but the DB has entries, those entries
    are returned — no re-download from a previously-tracked session."""
    from app.core.metadata_db import MetadataDB
    db = MetadataDB(str(tmp_path))
    db.mark_downloaded("999")
    db.close()
    s = load_exist_pid_set(str(tmp_path))
    assert "999" in s


def test_load_db_corruption_does_not_break_json_read(tmp_path):
    """If metadata.sqlite3 is corrupt, the function still returns the JSON set."""
    (tmp_path / "exist_pid.json").write_text('["100","200"]', encoding='utf-8')
    (tmp_path / "metadata.sqlite3").write_bytes(b"not a sqlite file")
    s = load_exist_pid_set(str(tmp_path))
    assert s == {"100", "200"}
