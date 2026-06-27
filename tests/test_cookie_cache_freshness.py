"""Tests for RunController._cookie_cache_is_fresh — the cache-skip predicate."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.gui.run_actions import RunController, _RETEST_INTERVAL_SEC
from app.core.settings_store import SettingsStore
from app.core.worker_event import WorkerEvent


def test_fresh_when_status_valid_and_recent():
    now = 1_000_000.0
    entry = {"status": "有效", "last_tested_at": now - 10}
    assert RunController._cookie_cache_is_fresh(entry, now) is True


def test_stale_when_status_valid_but_too_old():
    now = 1_000_000.0
    # tested 31 days ago > 30-day window
    entry = {"status": "有效", "last_tested_at": now - (_RETEST_INTERVAL_SEC + 1)}
    assert RunController._cookie_cache_is_fresh(entry, now) is False


def test_not_fresh_when_status_invalid():
    """Even a recent test doesn't make 失效 fresh — it must be re-tested."""
    now = 1_000_000.0
    entry = {"status": "失效", "last_tested_at": now - 10}
    assert RunController._cookie_cache_is_fresh(entry, now) is False


def test_not_fresh_when_status_unknown():
    now = 1_000_000.0
    entry = {"status": "未知", "last_tested_at": now - 10}
    assert RunController._cookie_cache_is_fresh(entry, now) is False


def test_not_fresh_when_status_missing():
    now = 1_000_000.0
    entry = {"last_tested_at": now - 10}
    assert RunController._cookie_cache_is_fresh(entry, now) is False


def test_not_fresh_when_no_timestamp():
    now = 1_000_000.0
    entry = {"status": "有效"}
    assert RunController._cookie_cache_is_fresh(entry, now) is False


def test_not_fresh_when_timestamp_is_none():
    now = 1_000_000.0
    entry = {"status": "有效", "last_tested_at": None}
    assert RunController._cookie_cache_is_fresh(entry, now) is False


def test_not_fresh_when_timestamp_garbage():
    now = 1_000_000.0
    entry = {"status": "有效", "last_tested_at": "not-a-number"}
    assert RunController._cookie_cache_is_fresh(entry, now) is False


def test_string_timestamp_accepted_when_numeric():
    """JSON load might leave timestamps as strings; accept those that parse."""
    now = 1_000_000.0
    entry = {"status": "有效", "last_tested_at": str(now - 100)}
    assert RunController._cookie_cache_is_fresh(entry, now) is True


def test_boundary_at_retest_interval():
    """Exactly at the boundary, the cache is considered stale."""
    now = 1_000_000.0
    entry = {"status": "有效", "last_tested_at": now - _RETEST_INTERVAL_SEC}
    assert RunController._cookie_cache_is_fresh(entry, now) is False


def test_refresh_cookie_timestamp_emits_cookie_status_event(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    base = tmp_path / "pixiv_download"
    store = SettingsStore(str(base))
    data = store.load()
    data["auth"]["cookies_entries"] = [
        {"cookie": "c1", "alias": "A", "status": "有效", "last_tested_at": 1.0}
    ]
    store.save(data)

    import queue

    class _View:
        _active_thread = None

    event_q = queue.Queue()
    controller = RunController(_View(), event_q)

    controller._refresh_cookie_timestamp("c1")

    ev = event_q.get_nowait()
    assert isinstance(ev, WorkerEvent)
    assert ev.type == "cookie_status"
    cookie, status, tested_at = ev.data
    assert cookie == "c1"
    assert status == "有效"
    assert float(tested_at) > 1.0
