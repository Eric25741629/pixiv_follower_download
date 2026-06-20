"""Tests for the `verify-files` CLI subcommand (one-time integrity scan)."""
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.cli import commands


JPEG_OK = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\x10" * 64 + b"\xff\xd9"
JPEG_TRUNC = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # header only, no footer


def _seed(tmp_path):
    """Create APPDATA + DB + download folder. Returns (base, dl_dir, db)."""
    base = os.path.join(str(tmp_path), "pixiv_download")
    os.makedirs(base, exist_ok=True)
    dl = os.path.join(str(tmp_path), "downloads")
    os.makedirs(dl, exist_ok=True)
    from app.core.metadata_db import MetadataDB
    db = MetadataDB(base)
    return base, dl, db


def _set_download_path(base, dl):
    from app.core.settings_store import SettingsStore
    s = SettingsStore(base)
    s.migrate_from_legacy()
    s.update_fields("download", {"path": dl})


def test_verify_files_reports_ok_and_truncated(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    base, dl, db = _seed(tmp_path)
    _set_download_path(base, dl)
    # Three downloaded pages: one OK, one truncated, one missing on disk.
    db.upsert_page("1111", 0, status="downloaded", url="https://i.pximg.net/1111_p0.jpg")
    db.upsert_page("2222", 0, status="downloaded", url="https://i.pximg.net/2222_p0.jpg")
    db.upsert_page("3333", 0, status="downloaded", url="https://i.pximg.net/3333_p0.jpg")
    db.close()
    # Files: matching the URL-derived filename convention so the folder scan maps them.
    with open(os.path.join(dl, "_PID1111p0_x.jpg"), "wb") as f:
        f.write(JPEG_OK)
    with open(os.path.join(dl, "_PID2222p0_x.jpg"), "wb") as f:
        f.write(JPEG_TRUNC)
    # 333 has no file at all.

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = commands.main(["verify-files", "--json"])
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["downloaded_pages"] == 3
    assert payload["ok"] == 1
    assert payload["truncated"] == 1
    assert payload["missing"] == 1
    assert payload["reset"] == 0  # no --fix


def test_verify_files_fix_marks_bad_pages_pending(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    base, dl, db = _seed(tmp_path)
    _set_download_path(base, dl)
    db.upsert_page("1111", 0, status="downloaded", url="https://i.pximg.net/1111_p0.jpg")
    db.upsert_page("2222", 0, status="downloaded", url="https://i.pximg.net/2222_p0.jpg")
    db.upsert_page("3333", 0, status="downloaded", url="https://i.pximg.net/3333_p0.jpg")
    db.close()
    with open(os.path.join(dl, "_PID1111p0_x.jpg"), "wb") as f:
        f.write(JPEG_OK)
    with open(os.path.join(dl, "_PID2222p0_x.jpg"), "wb") as f:
        f.write(JPEG_TRUNC)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = commands.main(["verify-files", "--fix", "--json"])
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["reset"] == 2  # 222 truncated + 333 missing

    # The two bad pages are now pending; the good one stays downloaded.
    from app.core.metadata_db import MetadataDB
    db2 = MetadataDB(base)
    try:
        assert db2.get_page("1111", 0)["status"] == "downloaded"
        assert db2.get_page("2222", 0)["status"] == "pending"
        assert db2.get_page("3333", 0)["status"] == "pending"
    finally:
        db2.close()


def test_verify_files_uses_db_file_path_when_present(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    base, dl, db = _seed(tmp_path)
    _set_download_path(base, dl)
    # file_path explicitly populated -> used directly, no folder-name convention needed.
    good = os.path.join(dl, "anything_unconventional.jpg")
    with open(good, "wb") as f:
        f.write(JPEG_OK)
    db.mark_page_downloaded("1111", 0, file_path=good, url="https://i.pximg.net/1111_p0.jpg")
    db.close()

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = commands.main(["verify-files", "--json"])
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["ok"] == 1
    assert payload["truncated"] == 0
    assert payload["missing"] == 0


def test_verify_files_human_output_to_stderr(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    base, dl, db = _seed(tmp_path)
    _set_download_path(base, dl)
    db.upsert_page("1111", 0, status="downloaded", url="https://i.pximg.net/1111_p0.jpg")
    db.close()
    with open(os.path.join(dl, "_PID1111p0_x.jpg"), "wb") as f:
        f.write(JPEG_OK)
    rc = commands.main(["verify-files"])  # no --json
    assert rc == 0
    captured = capsys.readouterr()
    # Human-readable summary goes to stderr; stdout stays empty without --json.
    assert "ok" in captured.err.lower() or "完整" in captured.err
    assert captured.out.strip() == ""
