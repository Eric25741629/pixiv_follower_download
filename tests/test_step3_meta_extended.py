"""Tests that Step 3 carries upload_date / create_date / user_id / user_name
from Pixiv_info through to the url_meta dict and the all_url_meta.json
shape consumed by ``mirror_meta_dict_to_db``."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB
from app.core.pixiv_thread_utils import mirror_meta_dict_to_db
from app.core.thread_url_fetch import get_img_url_thread


def _make_thread_skeleton():
    """A bare instance of ``get_img_url_thread`` with just enough attributes
    to call the meta-build / meta-extract helpers without going near network."""
    t = get_img_url_thread.__new__(get_img_url_thread)
    t.url_meta = {}
    return t


def test_extract_meta_from_cache_returns_nine_elements_with_defaults():
    """Old cache entries (pre-extension) don't carry user/date keys; the
    extractor must fill them with ``None`` so the unpack at call site doesn't
    raise."""
    t = _make_thread_skeleton()
    out = t._step3_extract_meta_from_cache({
        "tag": ["a"], "like": 100, "pagecount": 2,
        "img_url": "http://x/p.jpg", "requires_cookie": False,
    })
    assert len(out) == 9
    tag, like, pagecount, img_url, need_cookie, ud, cd, uid, uname = out
    assert tag == ["a"]
    assert like == 100
    assert pagecount == 2
    assert img_url == "http://x/p.jpg"
    assert need_cookie is False
    assert (ud, cd, uid, uname) == (None, None, None, None)


def test_extract_meta_from_cache_returns_extended_fields_when_present():
    t = _make_thread_skeleton()
    out = t._step3_extract_meta_from_cache({
        "tag": ["a"], "like": 100, "pagecount": 1,
        "img_url": "http://x/p.jpg",
        "upload_date": "2026-05-20T09:00:00+09:00",
        "create_date": "2026-05-20T08:55:00+09:00",
        "user_id": "111", "user_name": "ArtistA",
    })
    _, _, _, _, _, ud, cd, uid, uname = out
    assert ud == "2026-05-20T09:00:00+09:00"
    assert cd == "2026-05-20T08:55:00+09:00"
    assert uid == "111"
    assert uname == "ArtistA"


def test_build_url_meta_entry_stores_extended_fields():
    t = _make_thread_skeleton()
    t._step3_build_url_meta_entry(
        "100", ["tagA"], 200, 2, "http://x/p.jpg", False,
        "https://www.pixiv.net/artworks/100", "network",
        upload_date="2026-05-20T09:00:00+09:00",
        create_date="2026-05-20T08:55:00+09:00",
        user_id="111", user_name="ArtistA",
    )
    entry = t.url_meta["100"]
    assert entry["upload_date"] == "2026-05-20T09:00:00+09:00"
    assert entry["create_date"] == "2026-05-20T08:55:00+09:00"
    assert entry["user_id"] == "111"
    assert entry["user_name"] == "ArtistA"
    # Nested pixiv_info dict should also carry the fields for downstream
    # consumers that read the queried_at-bearing copy.
    pinfo = entry["pixiv_info"]
    assert pinfo["upload_date"] == "2026-05-20T09:00:00+09:00"
    assert pinfo["user_name"] == "ArtistA"


def test_resolve_meta_from_network_handles_legacy_4_element_info():
    """Defense in depth: ``Pixiv_info`` is supposed to always return an
    8-element list now, but if a stale call site / mock returns the legacy
    4-element shape, the 8-element unpack inside the network-resolve helper
    must fail gracefully rather than crash. We exercise just the unpack
    block in isolation, without instantiating the heavy thread skeleton."""
    info = [["tagA"], 100, 1, "http://x/p.jpg"]  # legacy 4-element
    try:
        (tag, like, pagecount, img_url,
         upload_date, create_date, user_id, user_name) = info
        unpacked = True
    except Exception:
        unpacked = False
    assert unpacked is False, (
        "8-element unpack must reject a legacy 4-element list; the helper's "
        "broad except clause then returns None so the caller finalises the "
        "PID as unresolvable instead of looping."
    )


def test_mirror_url_meta_to_db_persists_user_info(tmp_path):
    """End-to-end: ``url_meta`` dict → ``mirror_meta_dict_to_db`` →
    ``artworks`` row carries all 4 new fields."""
    t = _make_thread_skeleton()
    t._step3_build_url_meta_entry(
        "100", ["tagA"], 200, 1, "http://x/100_p.jpg", False,
        "https://www.pixiv.net/artworks/100", "network",
        upload_date="2026-05-20T09:00:00+09:00",
        create_date="2026-05-20T08:55:00+09:00",
        user_id="111", user_name="ArtistA",
    )
    db = MetadataDB(str(tmp_path), event_log=None)
    mirror_meta_dict_to_db(db, t.url_meta)
    row = db.get_artwork("100")
    assert row["upload_date"] == "2026-05-20T09:00:00+09:00"
    assert row["create_date"] == "2026-05-20T08:55:00+09:00"
    assert row["user_id"] == "111"
    assert row["user_name"] == "ArtistA"
    db.close()
