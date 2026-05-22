"""Unit tests for the daily-throttle logic in RunController._backup_db."""
from __future__ import annotations

import datetime
import os
from queue import Queue
from unittest.mock import patch, MagicMock


def _make_rc():
    from app.gui.run_actions import RunController
    rc = RunController.__new__(RunController)
    rc._event_q = Queue()
    rc._stats_collector = None
    rc._run_all_mode = False
    rc._event_log = None
    return rc


def test_backup_skipped_when_auto_snapshot_off(tmp_path):
    """auto_snapshot_on_run=False → backup_db never called."""
    from app.core.metadata_db import MetadataDB
    rc = _make_rc()
    cfg = {"auto_snapshot_on_run": False}
    with patch("app.gui.run_actions._read_event_log_cfg", return_value=cfg), \
         patch("app.gui.run_actions._write_event_log_cfg") as mock_write, \
         patch.object(MetadataDB, "backup_db") as mock_backup:
        rc._backup_db()
    mock_backup.assert_not_called()
    mock_write.assert_not_called()


def test_backup_skipped_when_already_ran_today(tmp_path):
    """last_snapshot_date == today → backup_db never called."""
    from app.core.metadata_db import MetadataDB
    rc = _make_rc()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cfg = {"auto_snapshot_on_run": True, "last_snapshot_date": today}
    with patch("app.gui.run_actions._read_event_log_cfg", return_value=cfg), \
         patch("app.gui.run_actions._write_event_log_cfg") as mock_write, \
         patch.object(MetadataDB, "backup_db") as mock_backup:
        rc._backup_db()
    mock_backup.assert_not_called()
    mock_write.assert_not_called()


def test_backup_runs_when_no_prior_date(tmp_path):
    """No last_snapshot_date → backup_db called and date persisted."""
    from app.core.metadata_db import MetadataDB
    rc = _make_rc()
    cfg = {}  # auto_snapshot_on_run defaults True, no date
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    MetadataDB(str(tmp_path)).meta_count()  # create DB file

    with patch("app.gui.run_actions._data_path", return_value=str(tmp_path) + os.sep), \
         patch("app.gui.run_actions._read_event_log_cfg", return_value=cfg), \
         patch("app.gui.run_actions._write_event_log_cfg") as mock_write, \
         patch.object(MetadataDB, "backup_db", return_value=True) as mock_backup:
        rc._backup_db()

    mock_backup.assert_called_once()
    written_cfg = mock_write.call_args[0][0]
    assert written_cfg.get("last_snapshot_date") == today


def test_backup_date_not_persisted_on_failure(tmp_path):
    """backup_db returning False → last_snapshot_date NOT written."""
    from app.core.metadata_db import MetadataDB
    rc = _make_rc()
    cfg = {}

    MetadataDB(str(tmp_path)).meta_count()

    with patch("app.gui.run_actions._data_path", return_value=str(tmp_path) + os.sep), \
         patch("app.gui.run_actions._read_event_log_cfg", return_value=cfg), \
         patch("app.gui.run_actions._write_event_log_cfg") as mock_write, \
         patch.object(MetadataDB, "backup_db", return_value=False):
        rc._backup_db()

    mock_write.assert_not_called()


def test_max_history_formula(tmp_path):
    """max_history = max(3, retention_days // 14); default 60 days → 4."""
    from app.core.metadata_db import MetadataDB
    rc = _make_rc()

    MetadataDB(str(tmp_path)).meta_count()

    captured = {}

    def fake_backup(max_history):
        captured["max_history"] = max_history
        return True

    cfg = {"retention_days": 60}
    with patch("app.gui.run_actions._data_path", return_value=str(tmp_path) + os.sep), \
         patch("app.gui.run_actions._read_event_log_cfg", return_value=cfg), \
         patch("app.gui.run_actions._write_event_log_cfg"), \
         patch.object(MetadataDB, "backup_db", side_effect=fake_backup):
        rc._backup_db()

    assert captured["max_history"] == 4  # max(3, 60 // 14) = max(3, 4) = 4


def test_max_history_floor_at_3(tmp_path):
    """Very short retention (14 days) → max_history floors at 3."""
    from app.core.metadata_db import MetadataDB
    rc = _make_rc()

    MetadataDB(str(tmp_path)).meta_count()

    captured = {}

    def fake_backup(max_history):
        captured["max_history"] = max_history
        return True

    cfg = {"retention_days": 14}
    with patch("app.gui.run_actions._data_path", return_value=str(tmp_path) + os.sep), \
         patch("app.gui.run_actions._read_event_log_cfg", return_value=cfg), \
         patch("app.gui.run_actions._write_event_log_cfg"), \
         patch.object(MetadataDB, "backup_db", side_effect=fake_backup):
        rc._backup_db()

    assert captured["max_history"] == 3  # max(3, 14 // 14) = max(3, 1) = 3
