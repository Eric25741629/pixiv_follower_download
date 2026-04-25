"""SettingsStore: single settings.json replacing 5 scattered config files.

Legacy files migrated automatically on first access:
  data.json, logging.json, othersettings.json, cookies.json, pass.json
  → all merged into settings.json, old files moved to trash/
"""
import copy
import json
import os

from app.core.pixiv_thread_utils import atomic_write_json, safe_read_json, trash_file


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
        "ai_gen_dir": False,
    },
    "performance": {
        "single_thread_mode": False,
        "pid_wait_min": 10,
        "pid_wait_max": 60,
        "pid_wait_nocookie_min": 1,
        "pid_wait_nocookie_max": 6,
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
    },
}

LEGACY_FILES = [
    "data.json",
    "logging.json",
    "othersettings.json",
    "cookies.json",
    "pass.json",
]


class SettingsStore:
    """Read/write unified settings.json with automatic legacy migration."""

    FILENAME = "settings.json"

    def __init__(self, base_path):
        self._base = str(base_path or "").strip()
        self._path = os.path.join(self._base, self.FILENAME)

    # ── public API ─────────────────────────────────────────────────────────

    def migrate_from_legacy(self):
        """If settings.json doesn't exist, build it from old files and trash them."""
        if os.path.isfile(self._path):
            # Already migrated; opportunistically trash any leftover legacy files.
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
        """Read-modify-write: replace one top-level section."""
        data = self.load()
        data[section_key] = section_data
        self.save(data)

    def update_fields(self, section_key, fields):
        """Read-modify-write: update specific fields within a section."""
        data = self.load()
        section = data.setdefault(section_key, copy.deepcopy(DEFAULTS.get(section_key, {})))
        section.update(fields)
        data[section_key] = section
        self.save(data)

    def update_multiple(self, sections_dict):
        """Read-modify-write: update several sections in a single write."""
        data = self.load()
        for key, section_data in sections_dict.items():
            data[key] = section_data
        self.save(data)

    # ── internal helpers ────────────────────────────────────────────────────

    def _merge_defaults(self, raw):
        merged = copy.deepcopy(DEFAULTS)
        for section_key, default_section in DEFAULTS.items():
            raw_section = raw.get(section_key)
            if isinstance(raw_section, dict):
                merged[section_key] = {**default_section, **raw_section}
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
        merged["directory"].update({
            "create_dir": bool(d.get("create_dir", False)),
            "no_R18G_dir": bool(d.get("no_R18G_dir", False)),
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
