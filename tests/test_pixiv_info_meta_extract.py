"""Tests for the user-info / date extractors and the extended Pixiv_info
return shape introduced when artworks gained upload_date / create_date /
user_id / user_name."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pixiv_api import (
    _extract_artwork_create_date,
    _extract_artwork_upload_date,
    _extract_artwork_user_id,
    _extract_artwork_user_name,
)


def test_extract_upload_date_present():
    body = {"uploadDate": "2024-03-15T10:00:00+09:00"}
    assert _extract_artwork_upload_date(body) == "2024-03-15T10:00:00+09:00"


def test_extract_upload_date_missing_returns_none():
    assert _extract_artwork_upload_date({}) is None
    assert _extract_artwork_upload_date({"uploadDate": ""}) is None
    assert _extract_artwork_upload_date({"uploadDate": None}) is None


def test_extract_create_date_present():
    body = {"createDate": "2024-03-15T09:55:00+09:00"}
    assert _extract_artwork_create_date(body) == "2024-03-15T09:55:00+09:00"


def test_extract_create_date_missing_returns_none():
    assert _extract_artwork_create_date({}) is None
    assert _extract_artwork_create_date({"createDate": ""}) is None


def test_extract_user_id_present():
    body = {"userId": "27915696"}
    assert _extract_artwork_user_id(body) == "27915696"


def test_extract_user_id_coerces_int_to_string():
    """Pixiv ajax returns userId as a string, but defensive: handle int too."""
    body = {"userId": 27915696}
    assert _extract_artwork_user_id(body) == "27915696"


def test_extract_user_id_missing_returns_none():
    assert _extract_artwork_user_id({}) is None
    assert _extract_artwork_user_id({"userId": ""}) is None
    assert _extract_artwork_user_id({"userId": None}) is None


def test_extract_user_name_present():
    body = {"userName": "畫師名稱"}
    assert _extract_artwork_user_name(body) == "畫師名稱"


def test_extract_user_name_missing_returns_none():
    assert _extract_artwork_user_name({}) is None
    assert _extract_artwork_user_name({"userName": ""}) is None
    assert _extract_artwork_user_name({"userName": None}) is None


def test_parse_payload_returns_eight_element_list():
    """Pixiv_info's internal _parse_payload (now closed over `id`) is exercised
    indirectly via a full mock of the inner steps. We just verify here that
    a manually-crafted response shape unpacks to 8 fields."""
    from app.core.pixiv_api import (
        _extract_artwork_body,
        _extract_artwork_img_url,
        _extract_artwork_pagecount,
        _extract_artwork_tags,
    )
    payload = {
        "body": {
            "bookmarkCount": 1234,
            "pageCount": 3,
            "uploadDate": "2024-01-15T12:00:00+09:00",
            "createDate": "2024-01-15T11:55:00+09:00",
            "userId": "12345678",
            "userName": "ArtistA",
            "tags": {"tags": [{"tag": "原创"}, {"tag": "ホロライブ"}]},
            "urls": {"original": "https://i.pximg.net/img-original/img/2024/01/15/12/00/00/103200000_p0.jpg"},
        }
    }
    body = _extract_artwork_body(payload)
    assert _extract_artwork_tags(body) == ["原创", "ホロライブ"]
    assert _extract_artwork_pagecount(body, "103200000") == 3
    assert _extract_artwork_upload_date(body) == "2024-01-15T12:00:00+09:00"
    assert _extract_artwork_create_date(body) == "2024-01-15T11:55:00+09:00"
    assert _extract_artwork_user_id(body) == "12345678"
    assert _extract_artwork_user_name(body) == "ArtistA"
    # _extract_artwork_img_url normalizes the multi-page template (p0 → p).
    assert _extract_artwork_img_url(body).endswith("103200000_p.jpg")


def test_extract_artwork_tags_dedups_preserving_first_occurrence():
    """Pixiv occasionally returns the same tag twice (e.g. one entry under a
    literal ``name`` key, another resolved via ``translation.en``). The
    extractor must dedupe while preserving the original order."""
    from app.core.pixiv_api import _extract_artwork_tags
    body = {
        "tags": {
            "tags": [
                {"tag": "原创"},
                {"tag": "ホロライブ"},
                {"tag": "原创"},          # exact-duplicate of the first entry
                {"name": "猫耳"},
                {"translated_name": "ホロライブ"},  # also resolves to ホロライブ
            ]
        }
    }
    assert _extract_artwork_tags(body) == ["原创", "ホロライブ", "猫耳"]


def test_extract_artwork_tags_dedups_ai_label_against_pixiv_tag():
    """If Pixiv's own tag list also contains the ``AI生成`` string, the
    prepended AI label wins (first occurrence) — no duplicate emitted."""
    from app.core.pixiv_api import _extract_artwork_tags
    body = {
        "aiType": 2,
        "tags": {"tags": [{"tag": "原创"}, {"tag": "AI生成"}]},
    }
    out = _extract_artwork_tags(body)
    assert out == ["AI生成", "原创"]
    assert out.count("AI生成") == 1


def test_extract_artwork_tags_case_sensitive_dedup():
    """Pixiv tags are case-sensitive (Hololive ≠ hololive), so dedup compares
    exact strings. Two casings of the same word survive as two distinct tags."""
    from app.core.pixiv_api import _extract_artwork_tags
    body = {"tags": {"tags": [{"tag": "Hololive"}, {"tag": "hololive"}]}}
    assert _extract_artwork_tags(body) == ["Hololive", "hololive"]


def test_pixiv_info_with_retry_returns_legacy_4_tuple(monkeypatch):
    """``_pixiv_info_with_retry`` must keep returning a 4-tuple even though
    ``Pixiv_info`` now produces an 8-element list — the legacy utility script
    (``get_download_url``) depends on that shape."""
    from app.core import pixiv_api

    def fake_pixiv_info(url, Agent=None, cookie=None, ip=None, *, session=None):
        return [["tagA"], 500, 2, "https://example/img_p0.jpg",
                "2024-01-15T12:00:00+09:00", "2024-01-15T11:55:00+09:00",
                "111", "ArtistA"]

    monkeypatch.setattr(pixiv_api, "Pixiv_info", fake_pixiv_info)
    out = pixiv_api._pixiv_info_with_retry("https://pixiv/x", "UA")
    assert out == (["tagA"], 500, 2, "https://example/img_p0.jpg")
