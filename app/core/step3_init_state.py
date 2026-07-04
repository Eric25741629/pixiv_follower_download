"""Per-run state construction for the Step 3 query engine ``get_img_url_thread``
(file-size refactor).

The queues/counters/paths initialised once per run, the cookie-requirement
trace load, and the step3_init diagnostic emit. Mixed into
``get_img_url_thread`` via ``_Step3InitStateMixin``; ``__init__`` calls these
through inheritance, so behaviour is unchanged. ``_safe_meta_count`` lives here
too (used by the init diag) and is re-imported back into ``thread_url_fetch``
for its other call site.
"""
from __future__ import annotations

import os
import threading
from queue import Queue

from app.core.pixiv_thread_utils import safe_read_json


def _safe_meta_count(db) -> int:
    try:
        return int(db.meta_count())
    except Exception:
        return 0


class _Step3InitStateMixin:
    def _init_step3_state(self):
        """Initialize per-run mutable Step 3 state (queues, counters, paths)."""
        self.tag_queue = Queue()
        self.like_queue = Queue()
        self._step3_filter_skip_counts = {"ban_tag": 0, "must_tag": 0, "like": 0}
        self._step3_filter_skip_notice_emitted = False
        self._step3_filter_skip_every = 200
        self._step3_query_counts = {"network": 0, "cache": 0, "skip": 0}
        self._step3_cookie_req_counts = {"need": 0, "free": 0, "unknown": 0}
        self._step3_wait_applied_count = 0
        self._step3_query_notice_every = 200
        self.url_meta = {}
        # PIDs whose url_meta row has already been mirrored to the SQLite cache
        # this run. Mirrors the _flushed_urls delta guard in _write_all_url_snapshot:
        # periodic/per-GIF flushes import only the un-flushed delta instead of the
        # whole (only-growing) dict, collapsing the O(N^2) re-import into O(N).
        # import_meta_dict is ON CONFLICT DO UPDATE / COALESCE, so the terminal
        # full-dict backstops re-write nothing new and the end state is identical.
        self._flushed_meta_pids = set()
        self.url_meta_path = os.path.join(self.path, "all_url_meta.json")
        self._pid_cache_hit = {}
        self._log_step3_cache_detail = False
        self._cookie_requirement_map = {}
        self.revoked_pid_path = os.path.join(self.path, "revoked_pid.txt")
        self._revoked_pid_set = set()
        self._revoked_pid_new = set()
        self._cookie_usage_counts = {"step3": {}, "step4": {}}
        self._cookie_usage_seen = {"step3": set(), "step4": set()}
        self._pending_pid_file_path = os.path.join(self.path, "pictures_id.txt")
        self._pending_pid_lock = threading.Lock()
        self._pending_pid_remaining = set()
        # Per-PID filter-decision cache; cleared by _apply_live_settings_if_changed
        # on a live settings change. Initialized here so the first mid-run change
        # does not hit an AttributeError (only the download thread defines it too).
        self._pid_filter_decision = {}

    def _load_cookie_requirement_cache(self):
        """Populate self._cookie_requirement_map from the saved trace JSON."""
        try:
            req_path = os.path.join(self.path, 'pixiv_cookie_requirement.json')
            req_data = safe_read_json(req_path, {})
            if isinstance(req_data, dict):
                for pid, entry in req_data.items():
                    if isinstance(entry, dict):
                        self._cookie_requirement_map[str(pid)] = entry.get('requires_cookie')
        except Exception:
            self._cookie_requirement_map = {}

    def _emit_step3_init_diag(self):
        """Append a step3_init diagnostic record with the per-filter counts."""
        self._diag(
            "step3_init",
            exist_pid_count=len(self.exist_pid),
            url_meta_count=_safe_meta_count(getattr(self, "_metadata_db", None)),
            like_min=int(self.like_num or 0),
            special_like_rule_count=len(self.special_like_rules),
            ban_tag_count=len(self._ban_tag_norm),
            must_tag_count=len(self._must_tag_norm),
            single_mode=bool(self.single_mode_flag),
        )
