"""Legacy cookie-requirement / url_meta schema migration for ``get_img_url_thread``.

The one-shot cookie-requirement file cluster (primary + history merge) and the
in-memory ``url_meta`` schema migration, mixed into ``get_img_url_thread`` via
``_Step3MigrationMixin``. Every method uses only ``self.`` for cross-method
calls (resolved through inheritance) plus the module-level names imported
below, so behavior is byte-for-byte identical to the originals. Sibling helpers
these methods call but do not own (``self.url_meta``, ``self.url_meta_path``,
``self._cookie_requirement_map``) live on the concrete class.
"""
from __future__ import annotations

import glob
import json
import os

from app.core.pixiv_thread_utils import (
    atomic_write_json,
    normalize_pid,
)


class _Step3MigrationMixin:
    """Cookie-requirement file merge + url_meta schema migration."""

    def _cookie_requirement_primary_paths(self):
        """Candidate paths for the canonical pixiv_cookie_requirement.json file."""
        candidates = []
        try:
            p = os.path.join(self.path, "pixiv_cookie_requirement.json")
            candidates.append(p)
        except Exception:
            pass
        try:
            appdata_root = os.path.join(os.getenv('APPDATA') or "", "pixiv_download")
            p = os.path.join(appdata_root, "pixiv_cookie_requirement.json")
            if p not in candidates:
                candidates.append(p)
        except Exception:
            pass
        return candidates

    def _cookie_requirement_history_paths(self, primary_candidates):
        """All sibling history/ backups for the given primary files, newest first."""
        history_candidates = []
        for base_file in primary_candidates:
            try:
                hist_dir = os.path.join(os.path.dirname(base_file), "history")
                pattern = os.path.join(hist_dir, os.path.basename(base_file) + ".*")
                found = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
                for fp in found:
                    if fp not in history_candidates:
                        history_candidates.append(fp)
            except Exception:
                pass
        return history_candidates

    @staticmethod
    def _read_cookie_requirement_json(file_path):
        """Read a cookie-requirement JSON file. Returns the dict or None on any failure."""
        try:
            if not os.path.isfile(file_path):
                return None
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _apply_cookie_requirement_entry(merged, pid_key, req):
        """Fold one (pid, req) into the merged dict using first-wins / None-upgrade rules."""
        if pid_key not in merged:
            merged[pid_key] = req
        elif merged.get(pid_key) is None and req in (True, False):
            merged[pid_key] = req

    def _merge_cookie_requirement_file(self, file_path, merged):
        """Read one cookie-requirement JSON and fold its entries into ``merged``.

        Earlier files (primary) win over later (history) — the caller orders
        the file list. A history backup may upgrade an existing ``None`` entry
        to a concrete True/False, but never overwrites a real value.
        """
        data = self._read_cookie_requirement_json(file_path)
        if data is None:
            return
        for raw_pid, entry in data.items():
            pid_key = normalize_pid(raw_pid)
            if not pid_key:
                continue
            req = entry.get("requires_cookie", None) if isinstance(entry, dict) else None
            if req not in (True, False, None):
                continue
            self._apply_cookie_requirement_entry(merged, pid_key, req)

    def _load_saved_cookie_requirement_map(self):
        # Only reached from _migrate_url_meta_schema, which now early-returns
        # when url_meta is empty (always the case in Steps 3/4 — meta is read
        # on-demand from the DB). So the costly primary+history parse of the
        # large pixiv_cookie_requirement.json files is skipped in the hot path;
        # the gap-fill-from-history contract below is preserved for the rare
        # case where in-memory url_meta is non-empty.
        merged = {}
        primary = self._cookie_requirement_primary_paths()
        history = self._cookie_requirement_history_paths(primary)
        for file_path in primary + history:
            self._merge_cookie_requirement_file(file_path, merged)
        return merged

    def _seed_cookie_requirement_map(self, saved_req_map):
        """Backfill self._cookie_requirement_map from the saved trace file."""
        try:
            if not isinstance(self._cookie_requirement_map, dict):
                self._cookie_requirement_map = {}
            for pid, req in saved_req_map.items():
                if pid not in self._cookie_requirement_map:
                    self._cookie_requirement_map[pid] = req
        except Exception:
            pass

    @staticmethod
    def _resolve_required_cookie_value(meta, pinfo, saved_req_map, pid_norm):
        """Pick the best available 'requires_cookie' for ``meta``.

        Priority: meta['requires_cookie'] → pinfo['requires_cookie'] → saved trace.
        Returns ``None`` when no source has a value.
        """
        req = meta.get("requires_cookie", None)
        if req is None and isinstance(pinfo, dict):
            req = pinfo.get("requires_cookie", None)
        if req is None and isinstance(saved_req_map, dict):
            req = saved_req_map.get(pid_norm, None)
        return req

    @staticmethod
    def _build_migrated_pixiv_info(meta, req):
        """Build the pixiv_info sub-dict from legacy top-level fields."""
        return {
            "tag": meta.get("tag", []) if isinstance(meta.get("tag"), list) else [],
            "like": meta.get("like", 0),
            "pagecount": meta.get("pagecount", 0),
            "img_url": meta.get("img_url", None),
            "requires_cookie": req,
            "queried_at": "",
            "source": "migrated",
        }

    _MIGRATE_SENTINEL = object()

    def _migrate_one_url_meta_entry(self, pid_key, meta, saved_req_map):
        """Migrate a single url_meta entry. Returns True iff anything changed."""
        if not isinstance(meta, dict):
            return False
        pid_norm = normalize_pid(pid_key) or str(pid_key)
        pinfo = meta.get("pixiv_info")
        req = self._resolve_required_cookie_value(meta, pinfo, saved_req_map, pid_norm)
        changed = False

        if meta.get("requires_cookie", self._MIGRATE_SENTINEL) != req:
            meta["requires_cookie"] = req
            changed = True

        if not isinstance(pinfo, dict):
            meta["pixiv_info"] = self._build_migrated_pixiv_info(meta, req)
            self.url_meta[pid_key] = meta
            return True

        if pinfo.get("requires_cookie", self._MIGRATE_SENTINEL) != req:
            pinfo["requires_cookie"] = req
            meta["pixiv_info"] = pinfo
            self.url_meta[pid_key] = meta
            changed = True
        return changed

    def _migrate_url_meta_schema(self):
        if not isinstance(self.url_meta, dict) or not self.url_meta:
            # Steps 3/4 load meta on-demand from the DB, so url_meta starts
            # empty — there is nothing to migrate. Returning early avoids the
            # (now history-skipped, but still non-trivial) cookie-requirement
            # file parse when no in-memory meta exists. The dedicated
            # _load_cookie_requirement_cache() still seeds the runtime map.
            return False
        try:
            saved_req_map = self._load_saved_cookie_requirement_map()
            self._seed_cookie_requirement_map(saved_req_map)
            changed = False
            for pid_key, meta in list(self.url_meta.items()):
                if self._migrate_one_url_meta_entry(pid_key, meta, saved_req_map):
                    changed = True
            if changed:
                atomic_write_json(self.url_meta_path, self.url_meta, backup=True)
        except Exception:
            return False
        return changed
