from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_combined import combined_thread


def test_combined_process_one_pid_refreshes_fetcher_and_downloader_before_work():
    t = combined_thread.__new__(combined_thread)
    calls = []

    class _Engine:
        def __init__(self, name):
            self.name = name

        def _apply_live_settings_if_changed(self):
            calls.append(self.name)

    class _Acc:
        cookie = "cookie"
        proxy_url = None

    t.fetcher = _Engine("fetcher")
    t.downloader = _Engine("downloader")
    t._last_pid_ok = False
    t._acquire_account = lambda: _Acc()
    t._release_account = lambda acc, ok=True: None
    t._run_with_network_retry = lambda label, fn: (True, fn(), None)
    t._download_only_urls = lambda pid: []

    result = t._process_one_pid("123", needs_query=False)

    assert calls == ["fetcher", "downloader"]
    assert result == []
