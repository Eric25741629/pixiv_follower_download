"""SettingsStore: single settings.json replacing 5 scattered config files.

Legacy files migrated automatically on first access:
  data.json, logging.json, othersettings.json, cookies.json, pass.json
  → all merged into settings.json, old files moved to trash/
"""
import copy
import json
import os
import threading

from app.core.pixiv_thread_utils import atomic_write_json, safe_read_json, trash_file


# Per-file locks shared across ALL SettingsStore instances pointing at the same
# settings.json. update_*()/migrate are load->modify->save sequences; without
# serialisation two threads writing different sections (e.g. the dispatcher's
# per-PID download_time write racing the worker's cookie-refresh auth write in
# combined mode) each save the whole dict and clobber the other section back to
# a stale snapshot. _store()/_settings_store() build a fresh instance per call,
# so the lock must live at module scope keyed by the resolved path, not on self.
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for_path(path: str) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


DEFAULTS = {
    "download": {
        "path": "",
        "like_num": 0,
        "r18_like_num": 0,
        "ban_tag": [],
        "must_tag": [],
        "download_time": "",
        "rule_tag_1": "",
        "rule_like_1": 0,
        "rule_tag_2": "",
        "rule_like_2": 0,
        "filename_template": "",
        # tag cleanup options applied to the {hashtag} placeholder in filenames.
        # Default false to preserve historical behavior.
        "tag_strip_brackets": False,
        "tag_strip_special_chars": False,
        # When true, a downloaded file's atime/mtime is set to the same
        # timetag that is embedded in its filename.
        "set_file_mtime": True,
        # When true, Step 4 downloads one author's works fully (PID desc)
        # before moving to the next author; unknown-author works go last.
        "author_order": False,
        # When true, Step 3 runs in 邊查邊下 (combined) mode: each PID is
        # downloaded immediately after its meta is fetched (merges Step 3+4).
        "combined_mode": False,
        # Concurrency for 邊查邊下: how many PIDs to query+download at once.
        # 1 = sequential (zero regression). Effective workers are capped by the
        # active account count and pending work, so K accounts -> up to K PIDs
        # in flight, each on its own cookie/proxy inside one cooldown window.
        "combined_workers": 1,
        # One-shot: when true, the next Step 2 run ignores the 30-day
        # "already scanned" skip and re-scans every artist, to backfill
        # user_id (author) for artists already inside the skip window.
        # _build_step2 consumes it (resets to False) after building.
        "force_full_rescan": False,
        # When Step 3 finds a cached artwork that was uploaded within this many
        # days, re-fetch its meta over the network instead of trusting the
        # cached like_count. 0 disables the feature.
        "rescrape_within_days": 365,
        # Main workflow source. "following" keeps the legacy flow
        # (following artists -> artist PIDs). "bookmarks" scans the current
        # user's bookmarked artworks instead.
        "source_mode": "following",
        # Pixiv following privacy scope for source_mode="following":
        # "public" -> rest=show, "private" -> rest=hide, "all" -> both.
        "following_scope": "all",
        # Pixiv bookmark privacy scope for source_mode="bookmarks":
        # "public" -> rest=show, "private" -> rest=hide, "all" -> both.
        "bookmark_scope": "all",
    },
    "filter": {
        "pass_tag": False,
        "pass_num": False,
        "hidefollow": False,
        "nogif": False,
        "notag": False,
        "notime": False,
    },
    "directory": {
        "create_dir": False,
        "no_R18G_dir": False,
        "no_R18_dir": False,
        "ai_gen_dir": False,
    },
    "performance": {
        "single_thread_mode": False,
        "pid_cooldown_avg": 35,
        "pid_wait_min": 10,
        "pid_wait_max": 60,
        "pid_wait_nocookie_min": 3,
        "pid_wait_nocookie_max": 8,
        "intra_pid_wait_min": 5,
        "intra_pid_wait_max": 15,
        # Total per-page download wall-clock budget (seconds). _stream_to_sink
        # aborts a trickling/wedged transfer past this so a single stalled image
        # can never freeze the whole pipeline (the 2026-06-21 hang). Generous by
        # default so legitimately large/slow images are not aborted; the per-recv
        # read timeout (30s) separately bounds a fully-silent socket.
        "download_deadline_sec": 120,
    },
    "jxl": {
        "enable": False,
        "cjxl_path": "",
        "delete_original": False,
        "effort": 7,
    },
    "auth": {
        "login_mode": 0,
        "agent": "",
        "userid": "",
        "account": "",
        "password": "",
        "cookies": "",
        "cookies_pool": [],
        "cookies_aliases": {},
        "cookies_entries": [],
        "proxy_pool": [],
        "cookie_proxy_map": {},
    },
    "ui": {
        "theme_mode": "SYSTEM",  # "LIGHT" | "DARK" | "SYSTEM"
        "language": "zh-TW",     # GUI locale; apply-on-restart. See app/i18n.py
    },
    "diagnostics": {
        # When true, the dispatcher writes the high-frequency per-event UI trace
        # (ui_events.log). Off by default — it fires once per WorkerEvent (with an
        # HTML-strip regex on output events) on the UI event-loop thread and only
        # mirrors events visible elsewhere. worker.log / download.log are always on.
        "verbose_logs": False,
    },
    "event_log": {
        "enabled": True,
        "retention_days": 60,
        "auto_snapshot_on_run": True,
        # Durability cadence: fsync every N events OR every interval seconds,
        # whichever comes first; anchor kinds (session.*/snapshot/checkpoint) and
        # close() always force an fsync. Batched defaults remove the per-DB-write
        # disk barrier that dominated write cost (set fsync_every_n=1 to restore
        # the legacy per-event fsync for maximum power-loss durability).
        "fsync_every_n": 200,
        "fsync_interval_sec": 1.0,
        # Hard ceiling on the events/ directory; oldest files are evicted first,
        # never past the most recent snapshot/shutdown/checkpoint anchor.
        "max_total_bytes": 4294967296,    # 4 GB
        # Roll the day's file to the next sequence once it exceeds this size.
        "rotate_size_bytes": 134217728,   # 128 MB
    },
    "schedule": {
        "enabled": False,
        "mode": "daily",          # "daily" | "interval"
        "time": "03:00",          # daily mode trigger, HH:MM 24h local
        "interval_hours": 6,       # interval mode period
        "action": "run_all",      # fixed: Run All (1 -> 2 -> combined)
    },
}

LEGACY_FILES = [
    "data.json",
    "logging.json",
    "othersettings.json",
    "cookies.json",
    "pass.json",
    # Phase 31-B: pixiv_info_cache.json was a duplicate of all_url_meta.json.
    # Trashed on next launch; all_url_meta.json is now the sole metadata cache.
    "pixiv_info_cache.json",
]


class SettingsStore:
    """Read/write unified settings.json with automatic legacy migration."""

    FILENAME = "settings.json"

    def __init__(self, base_path):
        self._base = str(base_path or "").strip()
        self._path = os.path.join(self._base, self.FILENAME)
        self._lock = _lock_for_path(self._path)

    # ── public API ─────────────────────────────────────────────────────────

    def migrate_from_legacy(self):
        """If settings.json doesn't exist, build it from old files and trash them."""
        with self._lock:
            if os.path.isfile(self._path):
                # Already migrated; opportunistically trash leftover legacy files.
                for fname in LEGACY_FILES:
                    old = os.path.join(self._base, fname)
                    if os.path.isfile(old):
                        trash_file(old, self._base)
                return
            merged = copy.deepcopy(DEFAULTS)
            self._import_data_json(merged)
            self._import_logging_json(merged)
            self._import_othersettings_json(merged)
            self._import_cookies_json(merged)
            self._import_pass_json(merged)
            atomic_write_json(self._path, merged, backup=False)
            for fname in LEGACY_FILES:
                old = os.path.join(self._base, fname)
                if os.path.isfile(old):
                    trash_file(old, self._base)

    def load(self):
        """Return full settings dict (merged with DEFAULTS for missing keys)."""
        raw = safe_read_json(self._path, {})
        if not isinstance(raw, dict):
            raw = {}
        return self._merge_defaults(raw)

    def save(self, data):
        """Overwrite settings.json with the given dict (no backup — contains auth)."""
        atomic_write_json(self._path, data, backup=False)

    def get_section(self, section_key, default=None):
        """Return one section dict, falling back to DEFAULTS."""
        data = self.load()
        section = data.get(section_key)
        if not isinstance(section, dict):
            return copy.deepcopy(DEFAULTS.get(section_key, default or {}))
        return section

    def update_section(self, section_key, section_data):
        """Read-modify-write: replace one top-level section (lock-serialised)."""
        with self._lock:
            data = self.load()
            data[section_key] = section_data
            self.save(data)

    def update_fields(self, section_key, fields):
        """Read-modify-write: update specific fields within a section (lock-serialised)."""
        with self._lock:
            data = self.load()
            section = data.setdefault(section_key, copy.deepcopy(DEFAULTS.get(section_key, {})))
            section.update(fields)
            data[section_key] = section
            self.save(data)

    def update_multiple(self, sections_dict):
        """Read-modify-write: update several sections in a single write (lock-serialised)."""
        with self._lock:
            data = self.load()
            for key, section_data in sections_dict.items():
                data[key] = section_data
            self.save(data)

    def mutate_section(self, section_key, mutator):
        """Atomically read-modify-write one section under the path lock.

        ``mutator(section_dict) -> section_dict`` runs WHILE the lock is held, so
        the read and the write are one critical section. Callers that did
        ``get_section()`` then later ``update_section()`` dropped the lock in
        between, so two concurrent cookie-status writers each loaded the old
        section and the slower one clobbered the faster one's edit. Route every
        such read-then-write through here instead."""
        with self._lock:
            data = self.load()
            section = data.get(section_key)
            if not isinstance(section, dict):
                section = copy.deepcopy(DEFAULTS.get(section_key, {}))
            data[section_key] = mutator(section)
            self.save(data)

    # ── internal helpers ────────────────────────────────────────────────────

    def _merge_defaults(self, raw):
        merged = copy.deepcopy(DEFAULTS)
        for section_key, default_section in DEFAULTS.items():
            raw_section = raw.get(section_key)
            if isinstance(raw_section, dict):
                merged[section_key] = {**default_section, **raw_section}
        # Migration: derive pid_cooldown_avg from old pid_wait_min/max if absent.
        raw_perf = raw.get("performance", {})
        if (
            isinstance(raw_perf, dict)
            and "pid_cooldown_avg" not in raw_perf
            and ("pid_wait_min" in raw_perf or "pid_wait_max" in raw_perf)
        ):
            try:
                old_min = int(raw_perf.get("pid_wait_min", 10))
                old_max = int(raw_perf.get("pid_wait_max", 60))
                avg = (old_min + old_max) // 2
                avg = max(5, min(300, avg))
                merged["performance"]["pid_cooldown_avg"] = avg
            except (TypeError, ValueError):
                pass
        raw_dl = raw.get("download", {})
        raw_filter = raw.get("filter", {})
        if (
            isinstance(raw_dl, dict)
            and "following_scope" not in raw_dl
            and isinstance(raw_filter, dict)
            and "hidefollow" in raw_filter
        ):
            merged["download"]["following_scope"] = (
                "public" if bool(raw_filter.get("hidefollow", False)) else "all"
            )
        return merged

    def _read_legacy(self, fname):
        path = os.path.join(self._base, fname)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _import_data_json(self, merged):
        d = self._read_legacy("data.json")
        if not isinstance(d, dict):
            return
        merged["download"].update({
            "path": d.get("user_download_path", ""),
            "like_num": d.get("like_num", 0),
            "r18_like_num": d.get("r18_like_num", 0),
            "ban_tag": d.get("ban_tag", []),
            "must_tag": d.get("must_tag", []),
            "download_time": d.get("download_time", ""),
            "rule_tag_1": d.get("rule_tag_1", ""),
            "rule_like_1": d.get("rule_like_1", 0),
            "rule_tag_2": d.get("rule_tag_2", ""),
            "rule_like_2": d.get("rule_like_2", 0),
        })

    def _import_logging_json(self, merged):
        d = self._read_legacy("logging.json")
        if not isinstance(d, dict):
            return
        merged["auth"]["login_mode"] = int(d.get("logging_mode", 0))

    def _import_othersettings_json(self, merged):
        d = self._read_legacy("othersettings.json")
        if not isinstance(d, dict):
            return
        merged["filter"].update({
            "hidefollow": bool(d.get("hidefollow", False)),
            "nogif": bool(d.get("nogif", False)),
            "notag": bool(d.get("notag", False)),
            "notime": bool(d.get("notime", False)),
        })
        merged["download"]["following_scope"] = (
            "public" if bool(d.get("hidefollow", False)) else "all"
        )
        merged["directory"].update({
            "create_dir": bool(d.get("create_dir", False)),
            "no_R18G_dir": bool(d.get("no_R18G_dir", False)),
            "no_R18_dir": bool(d.get("no_R18_dir", False)),
            "ai_gen_dir": bool(d.get("ai_gen_dir", False)),
        })
        merged["performance"].update({
            "single_thread_mode": bool(d.get("single_thread_mode", False)),
            "pid_wait_min": int(d.get("pid_wait_min", 10)),
            "pid_wait_max": int(d.get("pid_wait_max", 60)),
            "pid_wait_nocookie_min": int(d.get("pid_wait_nocookie_min", 1)),
            "pid_wait_nocookie_max": int(d.get("pid_wait_nocookie_max", 6)),
        })
        merged["jxl"].update({
            "enable": bool(d.get("jxl_enable", False)),
            "cjxl_path": str(d.get("jxl_cjxl_path", "")),
            "delete_original": bool(d.get("jxl_delete_original", False)),
            "effort": int(d.get("jxl_effort", 7)),
        })

    def _import_cookies_json(self, merged):
        d = self._read_legacy("cookies.json")
        if not isinstance(d, dict):
            return
        merged["auth"].update({
            "agent": str(d.get("agent", "")),
            "userid": str(d.get("userid", "")),
            "account": str(d.get("account", "")),
            "password": str(d.get("password", "")),
            "cookies": str(d.get("cookies", "")),
            "cookies_pool": d.get("cookies_pool", []) if isinstance(d.get("cookies_pool"), list) else [],
            "cookies_aliases": d.get("cookies_aliases", {}) if isinstance(d.get("cookies_aliases"), dict) else {},
            "cookies_entries": d.get("cookies_entries", []) if isinstance(d.get("cookies_entries"), list) else [],
        })

    def _import_pass_json(self, merged):
        d = self._read_legacy("pass.json")
        if not isinstance(d, dict):
            return
        merged["filter"].update({
            "pass_tag": bool(d.get("pass_tag", False)),
            "pass_num": bool(d.get("pass_num", False)),
        })
