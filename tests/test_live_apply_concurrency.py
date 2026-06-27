"""_apply_live_settings_if_changed must be self-serializing: Step 4 pool mode
and combined parallel mode call it from K worker threads, and a mid-run
「儲存設定」 changes the signature for all of them at once. Without the lock +
double-checked signature, every worker would run the ~25-attribute apply body
concurrently (torn writes). With the fix the body runs exactly once per change.
"""
from pathlib import Path
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.thread_download import download_thread


class _FakeLive:
    def signature(self):
        return "new-signature"

    def sections(self):
        return {}


def test_apply_live_settings_runs_body_once_under_concurrency():
    t = download_thread.__new__(download_thread)
    t._live = _FakeLive()
    t._live_sig = "stale"
    t._live_apply_lock = threading.Lock()

    calls = []
    calls_lock = threading.Lock()

    def fake_locked(live, sig):
        with calls_lock:
            calls.append(sig)
        t._live_sig = sig  # mimic the real body stamping the new signature

    t._apply_live_settings_locked = fake_locked

    start = threading.Barrier(8, timeout=5)

    def worker():
        start.wait()
        t._apply_live_settings_if_changed()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # Exactly one worker ran the apply body; the rest saw the updated signature
    # (early-out or the double-check under the lock) and returned.
    assert calls == ["new-signature"]
    assert t._live_sig == "new-signature"


def test_apply_live_settings_noop_when_signature_unchanged():
    t = download_thread.__new__(download_thread)
    t._live = _FakeLive()
    t._live_sig = "new-signature"  # already current
    t._live_apply_lock = threading.Lock()
    called = []
    t._apply_live_settings_locked = lambda live, sig: called.append(sig)
    t._apply_live_settings_if_changed()
    assert called == []  # early-out, body never entered
