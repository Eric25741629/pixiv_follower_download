"""Tests for R-18 / R-18G folder separation in download_thread."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


def _stub():
    t = download_thread.__new__(download_thread)
    return t


def test_r18g_detected_for_r18g_tag():
    t = _stub()
    assert t._is_r18g_artwork(["R-18G", "オリジナル"]) is True


def test_r18g_detected_for_gore_markers():
    t = _stub()
    assert t._is_r18g_artwork(["糞"]) is True
    assert t._is_r18g_artwork(["子宮脫"]) is True


def test_r18g_not_detected_for_plain_r18():
    t = _stub()
    assert t._is_r18g_artwork(["R-18", "オリジナル"]) is False


def test_r18_only_detected_for_plain_r18():
    t = _stub()
    assert t._is_r18_artwork(["R-18", "オリジナル"]) is True


def test_r18_only_not_triggered_when_r18g_present():
    t = _stub()
    # An R-18G work also tagged R-18 must NOT be classified as R-18-only
    assert t._is_r18_artwork(["R-18", "R-18G"]) is False


def test_r18_only_handles_lowercase_and_safe_input():
    t = _stub()
    assert t._is_r18_artwork(["r-18"]) is True
    assert t._is_r18_artwork([]) is False
    assert t._is_r18_artwork(404) is False
    assert t._is_r18_artwork(None) is False


def test_resolve_download_target_dir_picks_r18g_over_r18(tmp_path):
    t = _stub()
    t.create_dir = False
    t.download_path = str(tmp_path)
    t.no_R18G_dir = False
    t.no_R18_dir = False
    t.ai_gen_dir = False

    out = t._resolve_download_target_dir(["R-18", "R-18G"], "12345")
    assert out.endswith("R-18G")


def test_resolve_download_target_dir_picks_r18_when_no_r18g(tmp_path):
    t = _stub()
    t.create_dir = False
    t.download_path = str(tmp_path)
    t.no_R18G_dir = False
    t.no_R18_dir = False
    t.ai_gen_dir = False

    out = t._resolve_download_target_dir(["R-18"], "12345")
    assert out.endswith("R-18")
    # Should NOT land in R-18G
    assert "R-18G" not in Path(out).name


def test_resolve_download_target_dir_no_r18_subfolder_when_disabled(tmp_path):
    t = _stub()
    t.create_dir = False
    t.download_path = str(tmp_path)
    t.no_R18G_dir = False
    t.no_R18_dir = True   # user opted out of R-18 folder
    t.ai_gen_dir = False

    out = t._resolve_download_target_dir(["R-18"], "12345")
    assert Path(out) == Path(str(tmp_path))


def test_resolve_download_target_dir_falls_through_for_safe_artwork(tmp_path):
    t = _stub()
    t.create_dir = False
    t.download_path = str(tmp_path)
    t.no_R18G_dir = False
    t.no_R18_dir = False
    t.ai_gen_dir = False

    out = t._resolve_download_target_dir(["オリジナル"], "12345")
    assert Path(out) == Path(str(tmp_path))
