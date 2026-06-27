"""A legacy-closed sentinel row must not count as fetched meta.

``import_downloaded_set`` inserts artwork rows with the sentinel timestamp
``meta_updated_at = '0001-01-01 00:00:00'`` to mark a PID closed without any
real meta. ``get_meta`` / ``has_meta`` / ``meta_count`` filter on
``meta_updated_at IS NOT NULL`` and must ALSO exclude that sentinel, otherwise
a closed-only PID is mistaken for one whose meta has been fetched (and Step 3's
"needs network fetch" branch never fires). This mirrors the special-case
already present in ``export_meta_dict``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.metadata_db import MetadataDB


def test_sentinel_row_is_not_treated_as_fetched_meta(tmp_path):
    db = MetadataDB(str(tmp_path))
    db.import_downloaded_set(["12345"])

    assert db.get_meta("12345") is None
    assert db.has_meta("12345") is False
    assert db.meta_count() == 0
