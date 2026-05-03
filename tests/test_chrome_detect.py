"""Tests for Chrome UA detection (mocked registry / filesystem)."""
from pathlib import Path
import sys
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import():
    from app.core.chrome_detect import detect_chrome_ua, _read_from_registry, _read_from_appdata
    return detect_chrome_ua, _read_from_registry, _read_from_appdata


def test_detect_returns_none_when_no_chrome():
    detect_chrome_ua, _, _ = _import()
    with patch("app.core.chrome_detect._read_from_registry", return_value=None), \
         patch("app.core.chrome_detect._read_from_appdata", return_value=None):
        assert detect_chrome_ua() is None


def test_detect_returns_ua_string_from_registry():
    detect_chrome_ua, _, _ = _import()
    with patch("app.core.chrome_detect._read_from_registry", return_value="124.0.6367.91"), \
         patch("app.core.chrome_detect._read_from_appdata", return_value=None):
        ua = detect_chrome_ua()
    assert ua is not None
    assert "Chrome/124.0.6367.91" in ua
    assert ua.startswith("Mozilla/5.0")


def test_detect_falls_back_to_appdata():
    detect_chrome_ua, _, _ = _import()
    with patch("app.core.chrome_detect._read_from_registry", return_value=None), \
         patch("app.core.chrome_detect._read_from_appdata", return_value="123.0.6312.58"):
        ua = detect_chrome_ua()
    assert ua is not None
    assert "Chrome/123.0.6312.58" in ua


def test_registry_returns_none_when_winreg_missing():
    _, _read_from_registry, _ = _import()
    with patch.dict("sys.modules", {"winreg": None}):
        result = _read_from_registry()
    assert result is None


def test_appdata_returns_none_when_dir_missing(tmp_path):
    _, _, _read_from_appdata = _import()
    with patch("os.environ.get", return_value=str(tmp_path)):
        result = _read_from_appdata()
    assert result is None


def test_appdata_picks_latest_version(tmp_path):
    _, _, _read_from_appdata = _import()
    chrome_app = tmp_path / "Google" / "Chrome" / "Application"
    chrome_app.mkdir(parents=True)
    (chrome_app / "123.0.6312.58").mkdir()
    (chrome_app / "124.0.6367.91").mkdir()
    (chrome_app / "notaversion").mkdir()
    with patch("os.environ.get", return_value=str(tmp_path)):
        result = _read_from_appdata()
    assert result == "124.0.6367.91"
