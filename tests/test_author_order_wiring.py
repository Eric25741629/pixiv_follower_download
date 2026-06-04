"""Wiring tests: author_order kwarg plumbing + settings default."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread
from app.core.settings_store import DEFAULTS


def test_author_order_in_legacy_scalar_schema():
    keys = [k for k, _ in download_thread._LEGACY_SCALAR_KW_SCHEMA]
    assert "author_order" in keys


def test_author_order_kwarg_plumbs_into_overrides():
    overrides = {}
    download_thread._apply_legacy_scalar_kwargs({"author_order": True}, overrides)
    assert overrides["author_order"] is True


def test_author_order_default_false():
    assert DEFAULTS["download"]["author_order"] is False
