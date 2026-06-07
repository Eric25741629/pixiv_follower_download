"""Wiring: RunController._build_step2 forwards download.author_order into the
Step 2 thread (so the final pictures_id.txt regroup is gated by the same
switch as Step 4 / combined-mode ordering)."""
import os
import sys
from queue import Queue

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def _patch_common(monkeypatch, ra, tmp_path, author_order):
    class _FakeStore:
        def migrate_from_legacy(self):
            pass

        def get_section(self, name):
            return {
                "auth": {"userid": "1", "cookies": "c1"},
                "download": {"author_order": author_order, "path": str(tmp_path)},
                "filter": {},
                "performance": {},
                "directory": {},
                "jxl": {},
            }[name]

    monkeypatch.setattr(ra, "_store", lambda: _FakeStore())
    monkeypatch.setattr(ra, "_data_path", lambda: str(tmp_path))
    monkeypatch.setattr(ra.RunController, "_validate_cookies_for_step",
                        lambda self, a, ag, n: ["c1"])
    monkeypatch.setattr(ra.RunController, "_build_scheduler",
                        lambda self, *a, **k: None)
    monkeypatch.setattr(ra, "_load_author_list", lambda: ["123"])


def test_build_step2_passes_author_order_true(monkeypatch, tmp_path):
    import app.gui.run_actions as ra
    _patch_common(monkeypatch, ra, tmp_path, True)
    rc = ra.RunController(main_view=object(), event_q=Queue())
    t = rc._build_thread(2)
    assert t is not None
    assert t.author_order is True


def test_build_step2_passes_author_order_false(monkeypatch, tmp_path):
    import app.gui.run_actions as ra
    _patch_common(monkeypatch, ra, tmp_path, False)
    rc = ra.RunController(main_view=object(), event_q=Queue())
    t = rc._build_thread(2)
    assert t is not None
    assert t.author_order is False
