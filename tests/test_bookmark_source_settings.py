"""Download source settings for following-vs-bookmark PID collection."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.settings_store import SettingsStore


def test_defaults_use_following_source_and_all_bookmarks_scope(tmp_path):
    dl = SettingsStore(str(tmp_path)).get_section("download")
    assert dl["source_mode"] == "following"
    assert dl["following_scope"] == "all"
    assert dl["bookmark_scope"] == "all"


def test_existing_settings_merge_source_defaults(tmp_path):
    (tmp_path / "settings.json").write_text(
        '{"download": {"path": "D:/pixiv"}}',
        encoding="utf-8",
    )
    dl = SettingsStore(str(tmp_path)).get_section("download")
    assert dl["path"] == "D:/pixiv"
    assert dl["source_mode"] == "following"
    assert dl["following_scope"] == "all"
    assert dl["bookmark_scope"] == "all"


def test_existing_hidefollow_setting_maps_to_public_following_scope(tmp_path):
    (tmp_path / "settings.json").write_text(
        '{"filter": {"hidefollow": true}, "download": {"path": "D:/pixiv"}}',
        encoding="utf-8",
    )
    dl = SettingsStore(str(tmp_path)).get_section("download")
    assert dl["following_scope"] == "public"
