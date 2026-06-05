import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.cli import commands


def test_status_json_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # Seed a DB with one pending + one downloaded page.
    base = os.path.join(str(tmp_path), "pixiv_download")
    os.makedirs(base, exist_ok=True)
    from app.core.metadata_db import MetadataDB
    db = MetadataDB(base)
    db.upsert_page("123", 0, status="pending", url="https://x/123_p0.jpg")
    db.upsert_page("456", 0, status="downloaded", url="https://x/456_p0.jpg")
    db.close()

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = commands.main(["status", "--json"])
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["pending_pages"] == 1
    assert payload["downloaded_pages"] == 1
    assert "db_path" in payload
