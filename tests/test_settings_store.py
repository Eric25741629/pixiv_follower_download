"""Tests for SettingsStore: migration, read/write, defaults (Phase 17)."""
from pathlib import Path
import sys
import json
import copy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.settings_store import SettingsStore, DEFAULTS, LEGACY_FILES


# ── helpers ──────────────────────────────────────────────────────────────────

def _write(tmp_path, fname, data):
    (tmp_path / fname).write_text(json.dumps(data), encoding="utf-8")


def _read_settings(tmp_path):
    f = tmp_path / "settings.json"
    assert f.exists(), "settings.json not created"
    return json.loads(f.read_text(encoding="utf-8"))


# ── load / save ───────────────────────────────────────────────────────────────

def test_load_missing_returns_defaults(tmp_path):
    store = SettingsStore(str(tmp_path))
    data = store.load()
    assert "download" in data
    assert "auth" in data
    assert data["jxl"]["effort"] == 7


def test_default_download_time_start_is_2026(tmp_path):
    """Fresh installs default the timetag start to 2026-01-01, not the epoch."""
    assert DEFAULTS["download"]["download_time"] == "2026-01-01 00:00:00"
    data = SettingsStore(str(tmp_path)).load()
    assert data["download"]["download_time"] == "2026-01-01 00:00:00"


def test_load_existing_settings(tmp_path):
    _write(tmp_path, "settings.json", {"download": {"path": "/my/path", "like_num": 500}})
    store = SettingsStore(str(tmp_path))
    d = store.get_section("download")
    assert d["path"] == "/my/path"
    assert d["like_num"] == 500


def test_save_round_trip(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.save({"download": {"path": "/foo"}})
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data["download"]["path"] == "/foo"


def test_get_section_returns_defaults_for_missing(tmp_path):
    store = SettingsStore(str(tmp_path))
    j = store.get_section("jxl")
    assert j["effort"] == DEFAULTS["jxl"]["effort"]


def test_update_section(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update_section("jxl", {"enable": True, "effort": 5, "cjxl_path": "x", "delete_original": False})
    j = store.get_section("jxl")
    assert j["enable"] is True
    assert j["effort"] == 5


def test_update_fields_partial(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update_section("auth", {"login_mode": 0, "agent": "UA", "userid": "123"})
    store.update_fields("auth", {"login_mode": 2})
    a = store.get_section("auth")
    assert a["login_mode"] == 2
    assert a["agent"] == "UA"   # not overwritten


def test_update_multiple_sections(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update_multiple({
        "performance": {"single_thread_mode": True, "pid_wait_min": 5,
                        "pid_wait_max": 30, "pid_wait_nocookie_min": 0, "pid_wait_nocookie_max": 3},
        "jxl": {"enable": True, "effort": 9, "cjxl_path": "", "delete_original": True},
    })
    p = store.get_section("performance")
    assert p["single_thread_mode"] is True
    j = store.get_section("jxl")
    assert j["effort"] == 9


# ── migrate_from_legacy ───────────────────────────────────────────────────────

def test_migration_creates_settings_json(tmp_path):
    _write(tmp_path, "data.json", {"user_download_path": "/dl", "like_num": 200})
    store = SettingsStore(str(tmp_path))
    store.migrate_from_legacy()
    assert (tmp_path / "settings.json").exists()
    assert not (tmp_path / "data.json").exists()


def test_migration_data_json(tmp_path):
    _write(tmp_path, "data.json", {
        "user_download_path": "/my/downloads",
        "like_num": 300,
        "r18_like_num": 50,
        "ban_tag": ["explicit"],
        "must_tag": ["fanart"],
        "download_time": "2025-01-01 00:00:00",
        "rule_tag_1": "cute",
        "rule_like_1": 100,
        "rule_tag_2": "",
        "rule_like_2": 0,
    })
    SettingsStore(str(tmp_path)).migrate_from_legacy()
    data = _read_settings(tmp_path)
    assert data["download"]["path"] == "/my/downloads"
    assert data["download"]["like_num"] == 300
    assert data["download"]["ban_tag"] == ["explicit"]


def test_migration_logging_json(tmp_path):
    _write(tmp_path, "logging.json", {"logging_mode": 2})
    SettingsStore(str(tmp_path)).migrate_from_legacy()
    data = _read_settings(tmp_path)
    assert data["auth"]["login_mode"] == 2


def test_migration_othersettings_json(tmp_path):
    _write(tmp_path, "othersettings.json", {
        "hidefollow": True, "nogif": False, "notag": True, "notime": False,
        "create_dir": True, "no_R18G_dir": True, "ai_gen_dir": False,
        "single_thread_mode": True, "pid_wait_min": 5, "pid_wait_max": 30,
        "pid_wait_nocookie_min": 0, "pid_wait_nocookie_max": 3,
        "jxl_enable": True, "jxl_cjxl_path": "/bin/cjxl",
        "jxl_delete_original": True, "jxl_effort": 9,
    })
    SettingsStore(str(tmp_path)).migrate_from_legacy()
    data = _read_settings(tmp_path)
    assert data["filter"]["hidefollow"] is True
    assert data["directory"]["create_dir"] is True
    assert data["performance"]["single_thread_mode"] is True
    assert data["jxl"]["enable"] is True
    assert data["jxl"]["cjxl_path"] == "/bin/cjxl"
    assert data["jxl"]["effort"] == 9


def test_migration_cookies_json(tmp_path):
    _write(tmp_path, "cookies.json", {
        "agent": "MyUA", "userid": "42", "account": "user@example.com",
        "password": "secret", "cookies": "SESS=abc",
        "cookies_pool": ["SESS=abc"], "cookies_aliases": {}, "cookies_entries": [],
    })
    SettingsStore(str(tmp_path)).migrate_from_legacy()
    data = _read_settings(tmp_path)
    assert data["auth"]["userid"] == "42"
    assert data["auth"]["agent"] == "MyUA"
    assert data["auth"]["cookies"] == "SESS=abc"


def test_migration_pass_json(tmp_path):
    _write(tmp_path, "pass.json", {"pass_tag": True, "pass_num": False})
    SettingsStore(str(tmp_path)).migrate_from_legacy()
    data = _read_settings(tmp_path)
    assert data["filter"]["pass_tag"] is True
    assert data["filter"]["pass_num"] is False


def test_migration_trashes_all_legacy_files(tmp_path):
    for fname in LEGACY_FILES:
        _write(tmp_path, fname, {})
    SettingsStore(str(tmp_path)).migrate_from_legacy()
    for fname in LEGACY_FILES:
        assert not (tmp_path / fname).exists(), f"{fname} should be trashed"
    trash_files = list((tmp_path / "trash").iterdir())
    assert len(trash_files) == len(LEGACY_FILES)


def test_migration_skipped_if_settings_exists(tmp_path):
    _write(tmp_path, "settings.json", {"download": {"path": "/existing"}})
    _write(tmp_path, "data.json", {"user_download_path": "/should-not-overwrite"})
    SettingsStore(str(tmp_path)).migrate_from_legacy()
    # settings.json should be unchanged (migration skipped)
    data = _read_settings(tmp_path)
    assert data["download"]["path"] == "/existing"
    # data.json should be trashed
    assert not (tmp_path / "data.json").exists()


def test_migration_missing_files_are_ignored(tmp_path):
    # Only one file exists
    _write(tmp_path, "pass.json", {"pass_tag": True, "pass_num": True})
    SettingsStore(str(tmp_path)).migrate_from_legacy()
    assert (tmp_path / "settings.json").exists()
    data = _read_settings(tmp_path)
    assert data["filter"]["pass_tag"] is True


# --- new tests for pid_cooldown_avg, proxy_pool, cookie_proxy_map ---

def test_defaults_include_pid_cooldown_avg(tmp_path):
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert "pid_cooldown_avg" in perf
    assert perf["pid_cooldown_avg"] == 35


def test_defaults_include_proxy_pool(tmp_path):
    store = SettingsStore(str(tmp_path))
    auth = store.get_section("auth")
    assert "proxy_pool" in auth
    assert auth["proxy_pool"] == []


def test_defaults_include_cookie_proxy_map(tmp_path):
    store = SettingsStore(str(tmp_path))
    auth = store.get_section("auth")
    assert "cookie_proxy_map" in auth
    assert auth["cookie_proxy_map"] == {}


def test_migration_derives_avg_from_min_max(tmp_path):
    import json
    data = {
        "performance": {
            "single_thread_mode": False,
            "pid_wait_min": 20,
            "pid_wait_max": 80,
        },
        "auth": {},
    }
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data))
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    # average of 20+80 = 50
    assert perf["pid_cooldown_avg"] == 50


def test_migration_clamps_avg_to_300_max(tmp_path):
    import json
    data = {
        "performance": {
            "pid_wait_min": 500,
            "pid_wait_max": 700,
        },
    }
    (tmp_path / "settings.json").write_text(json.dumps(data))
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["pid_cooldown_avg"] == 300


def test_migration_clamps_avg_to_5_min(tmp_path):
    import json
    data = {
        "performance": {
            "pid_wait_min": 1,
            "pid_wait_max": 1,
        },
    }
    (tmp_path / "settings.json").write_text(json.dumps(data))
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["pid_cooldown_avg"] == 5


def test_round_trip_pid_cooldown_avg(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update_fields("performance", {"pid_cooldown_avg": 45})
    perf = store.get_section("performance")
    assert perf["pid_cooldown_avg"] == 45


def test_round_trip_proxy_pool(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update_fields("auth", {"proxy_pool": ["http://1.1.1.1:80"]})
    auth = store.get_section("auth")
    assert auth["proxy_pool"] == ["http://1.1.1.1:80"]


def test_round_trip_cookie_proxy_map(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update_fields("auth", {"cookie_proxy_map": {"abc": "socks5://x:1080"}})
    auth = store.get_section("auth")
    assert auth["cookie_proxy_map"] == {"abc": "socks5://x:1080"}


def test_migration_skipped_when_no_legacy(tmp_path):
    """Empty settings dir gets default avg, not migrated."""
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["pid_cooldown_avg"] == 35


def test_migration_skipped_when_avg_already_set(tmp_path):
    """If pid_cooldown_avg is already saved, do not overwrite from min/max."""
    import json
    data = {
        "performance": {
            "pid_wait_min": 20,
            "pid_wait_max": 80,
            "pid_cooldown_avg": 100,
        },
    }
    (tmp_path / "settings.json").write_text(json.dumps(data))
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["pid_cooldown_avg"] == 100


def test_migration_skipped_when_avg_is_default_value_but_explicitly_set(tmp_path):
    """avg==35 saved explicitly: must NOT be overwritten by migration.

    Guards against a refactor regression where the migration condition
    becomes ``"pid_cooldown_avg" not in perf or perf == 35`` (which the
    plan spec originally proposed but is wrong: 35 is also the DEFAULTS
    value, making it indistinguishable from absence post-merge).
    """
    import json
    data = {
        "performance": {
            "pid_wait_min": 20,
            "pid_wait_max": 80,
            "pid_cooldown_avg": 35,
        },
    }
    (tmp_path / "settings.json").write_text(json.dumps(data))
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["pid_cooldown_avg"] == 35  # not 50


def test_intra_pid_wait_defaults(tmp_path):
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["intra_pid_wait_min"] == 5
    assert perf["intra_pid_wait_max"] == 15


def test_nocookie_wait_defaults_bumped(tmp_path):
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["pid_wait_nocookie_min"] == 3
    assert perf["pid_wait_nocookie_max"] == 8


def test_new_keys_merged_into_old_settings(tmp_path):
    """Existing settings.json without new keys still gets defaults via _merge_defaults."""
    (tmp_path / "settings.json").write_text(
        '{"performance": {"pid_cooldown_avg": 40}}', encoding="utf-8"
    )
    store = SettingsStore(str(tmp_path))
    perf = store.get_section("performance")
    assert perf["intra_pid_wait_min"] == 5
    assert perf["intra_pid_wait_max"] == 15


# ── concurrency: read-modify-write must not lose cross-section updates ──────
#
# Regression for the combined-mode (邊查邊下 / Step 3+4) cookie-refresh bug.
# During a run two threads write settings.json with no synchronisation:
#   * dispatcher thread, every PID: handle_timechanged ->
#     update_fields("download", ...)  (combined emits "timechanged" per PID), and
#   * worker thread, on each cookie's first success: _refresh_cookie_timestamp
#     -> update_section("auth", ...).
# Each does load() -> modify ONE section -> save() the WHOLE file, so whichever
# saves last reverts the other thread's section to its stale snapshot. The
# visible symptom is the cookie last_tested_at refresh (auth) being clobbered by
# a download write -> the cookies view shows stale state ("cookies 沒有正常刷新").

class _PausingStore(SettingsStore):
    """SettingsStore whose first load() parks mid read-modify-write.

    Lets a test deterministically interleave two writers: this store loads,
    parks (still inside its update_* call), a second writer completes a full RMW
    on a different section, then this one resumes and saves. Separate instances
    also prove the lock is shared per-file, not per-instance.
    """

    def __init__(self, base_path, paused, resume):
        super().__init__(base_path)
        self._paused = paused
        self._resume = resume
        self._armed = True

    def load(self):
        data = super().load()
        if self._armed:
            self._armed = False
            self._paused.set()
            self._resume.wait(5.0)
        return data


def test_concurrent_section_writes_do_not_clobber(tmp_path):
    import threading
    import time

    base = str(tmp_path)
    seed = SettingsStore(base)
    seed.save(seed._merge_defaults({
        "auth": {"cookies_entries": [
            {"cookie": "c", "status": "有效", "last_tested_at": 1.0},
        ]},
        "download": {"download_time": "OLD"},
    }))

    paused, resume = threading.Event(), threading.Event()
    a_store = _PausingStore(base, paused, resume)   # writes auth, parks mid-RMW
    b_store = SettingsStore(base)                    # writes download, separate instance

    def writer_a():
        a_store.update_section("auth", {"cookies_entries": [
            {"cookie": "c", "status": "有效", "last_tested_at": 999.0},
        ]})

    ta = threading.Thread(target=writer_a)
    ta.start()
    assert paused.wait(5.0), "writer A never reached its mid-RMW pause"

    tb = threading.Thread(
        target=lambda: b_store.update_fields("download", {"download_time": "NEW"})
    )
    tb.start()
    time.sleep(0.25)  # B finishes (unlocked) or blocks on the lock (locked)
    resume.set()
    ta.join(5.0)
    tb.join(5.0)

    final = SettingsStore(base)
    # B's download write must survive (committed while A was parked).
    assert final.get_section("download")["download_time"] == "NEW"
    # A's auth write must also be present.
    assert final.get_section("auth")["cookies_entries"][0]["last_tested_at"] == 999.0


def test_parallel_writers_lose_no_updates(tmp_path):
    """Stress: two threads each bump their own section's counter; with proper
    serialisation every increment survives (final counter == iteration count)."""
    import threading

    base = str(tmp_path)
    seed = SettingsStore(base)
    data = seed._merge_defaults({})
    data["auth"] = {"n": 0}
    data["download"] = {"n": 0}
    seed.save(data)

    iters = 60

    def bump(section):
        for _ in range(iters):
            store = SettingsStore(base)
            cur = store.get_section(section).get("n", 0)
            store.update_fields(section, {"n": cur + 1})

    threads = [
        threading.Thread(target=bump, args=("auth",)),
        threading.Thread(target=bump, args=("download",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20.0)

    final = SettingsStore(base)
    assert final.get_section("auth")["n"] == iters
    assert final.get_section("download")["n"] == iters
