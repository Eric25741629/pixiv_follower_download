"""Cookie-requirement + cookie-label + GIF cookie-usage helpers for the Step 3
query engine ``get_img_url_thread`` (file-size refactor).

Resolving which cookie label to show for a PID, remembering per-PID
requires_cookie state, and stamping/emitting/persisting the "this PID used a
cookie" fact after a gif fetch. Mixed into ``get_img_url_thread`` via
``_Step3CookieLabelsMixin``; every method reaches worker state through
inheritance (``self.url_meta`` / ``self._get_meta`` / ``self.cookie_pool`` /
``self._cookie_alias_map`` / ``self._pid_cookie_*`` / ``self._cookie_requirement_map``
/ ``self._persist_url_meta_with_fallback``), so behaviour is unchanged.
"""
from __future__ import annotations

import datetime

import pixiv_api

from app.core.pixiv_thread_utils import cookie_usage_label, normalize_pid


class _Step3CookieLabelsMixin:
    def _set_requires_cookie_meta(self, pid, need_cookie):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            meta = dict(self._get_meta(pid_key))
            meta["requires_cookie"] = need_cookie
            pixiv_info = meta.get("pixiv_info")
            if isinstance(pixiv_info, dict):
                pixiv_info["requires_cookie"] = need_cookie
                meta["pixiv_info"] = pixiv_info
            self.url_meta[pid_key] = meta
        except Exception:
            pass

    def _cookie_label_from_alias_selection(self, pid_key):
        """Try the per-PID alias map first (set by _select_cookie_for_pid)."""
        try:
            alias = str(self._pid_cookie_alias_selection.get(pid_key, "") or "").strip()
            return alias or None
        except Exception:
            return None

    def _cookie_label_from_pid_selection(self, pid_key):
        """Resolve the label for the cookie remembered for this PID."""
        try:
            selected = str(self._pid_cookie_selection.get(pid_key, "") or "").strip()
            if not selected:
                return None
            resolved = cookie_usage_label(selected, self.cookie_pool, self._cookie_alias_map)
            return resolved or None
        except Exception:
            return None

    def _cookie_label_from_pool_first(self):
        """Fallback: label of the pool's first cookie."""
        try:
            if not self.cookie_pool:
                return None
            resolved = cookie_usage_label(
                self.cookie_pool[0], self.cookie_pool, self._cookie_alias_map,
            )
            return resolved or None
        except Exception:
            return None

    def _cookie_label_default(self, need_cookie):
        """Final fallback when no cookie source is available."""
        single_cookie = str(getattr(self, "cookies", "") or "").strip()
        if need_cookie is False and not single_cookie:
            return "免Cookie"
        if single_cookie:
            return "單一Cookie"
        return "未提供Cookie"

    def _cookie_label_for_pid(self, pid, need_cookie=None):
        pid_key = normalize_pid(pid) or str(pid)
        for resolver in (
            lambda: self._cookie_label_from_alias_selection(pid_key),
            lambda: self._cookie_label_from_pid_selection(pid_key),
            lambda: self._cookie_label_from_pool_first(),
        ):
            label = resolver()
            if label:
                return label
        return self._cookie_label_default(need_cookie)

    def _refresh_cookie_requirement(self, pid, fallback=None):
        pid_key = normalize_pid(pid)
        if not pid_key:
            return fallback
        try:
            if isinstance(getattr(self, '_cookie_requirement_map', None), dict) and pid_key in self._cookie_requirement_map:
                return self._cookie_requirement_map.get(pid_key)
        except Exception:
            pass

        latest = fallback
        if latest is None:
            try:
                latest = pixiv_api.get_pixiv_cookie_requirement(pid_key)
            except Exception:
                latest = fallback
        try:
            if not isinstance(getattr(self, '_cookie_requirement_map', None), dict):
                self._cookie_requirement_map = {}
            self._cookie_requirement_map[pid_key] = latest
        except Exception:
            pass
        return latest

    def _stamp_gif_cookie_usage_in_meta(self, pid_key, source):
        """Mark requires_cookie + cookie_used fields on the in-memory url_meta entry."""
        try:
            self._set_requires_cookie_meta(pid_key, True)
            meta = dict(self._get_meta(pid_key))
            meta["cookie_used"] = True
            meta["cookie_used_source"] = str(source)
            meta["cookie_used_updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.url_meta[pid_key] = meta
        except Exception:
            pass

    def _emit_gif_cookie_usage_signal(self, pid_key, source):
        """Best-effort emit via the legacy ._output signal (Qt5 compat)."""
        signal_obj = self.__dict__.get("_output", None)
        if signal_obj is None:
            try:
                signal_obj = getattr(self, "_output", None)
            except Exception:
                signal_obj = None
        try:
            if signal_obj is not None and hasattr(signal_obj, "emit"):
                signal_obj.emit(
                    f"<p><font color='blue'>[GIF][Cookie] PID {pid_key} "
                    f"使用 cookies（來源：{source}），已更新 all_url_meta 暫存</font></p>"
                )
        except Exception:
            pass

    def _mark_gif_cookie_usage(self, pid, used, source="unknown"):
        pid_key = normalize_pid(pid) or str(pid)
        used_flag = bool(used)
        try:
            self._pid_cookie_used[pid_key] = used_flag
        except Exception:
            pass
        if not used_flag:
            return
        self._stamp_gif_cookie_usage_in_meta(pid_key, source)
        self._persist_url_meta_with_fallback(pid_key=pid_key)
        self._emit_gif_cookie_usage_signal(pid_key, source)
