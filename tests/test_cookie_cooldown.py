from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pixiv_thread import download_thread


def _build_thread_stub():
    t = download_thread.__new__(download_thread)
    t.cookies = "PHPSESSID=dummy"
    t.cookie_pool = []
    t.url_meta = {}
    t._pid_cookie_used = {}
    return t


def test_cookie_used_map_has_priority_over_requires_cookie_false():
    t = _build_thread_stub()
    pid = "123456"
    t.url_meta[pid] = {"requires_cookie": False}
    t._pid_cookie_used[pid] = True

    assert t._is_cookie_used_for_pid(pid) is True


def test_requires_cookie_fallback_when_pid_usage_unknown():
    t = _build_thread_stub()

    pid_true = "111"
    t.url_meta[pid_true] = {"requires_cookie": True}
    assert t._is_cookie_used_for_pid(pid_true) is True

    pid_false = "222"
    t.url_meta[pid_false] = {"requires_cookie": False}
    assert t._is_cookie_used_for_pid(pid_false) is False
