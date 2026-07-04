"""Cached-meta prefilter + rescrape-window freshness for the Step 3 query engine
``get_img_url_thread`` (file-size refactor).

Two related groups, mixed into ``get_img_url_thread`` via
``_Step3CachePrefilterMixin``:

1. The cache prefilter — deciding which pending PIDs can be satisfied straight
   from ``url_meta`` (expanding a cached img_url into per-page URLs, re-checking
   requires_cookie, applying the artwork filter) versus which still need a
   network fetch. Combined mode's cache-hit path runs through
   ``_prefilter_step3_with_cache`` (guarded by
   ``tests/test_combined_cache_hit_no_network.py``).
2. The rescrape window — whether a well-formed cache entry is "fresh" enough to
   short-circuit the network fetch, or recent enough (``_rescrape_within_days``)
   that we re-fetch to update the like count.

Every method reaches worker state through inheritance (``self.url_meta`` /
``self._get_meta`` / ``self._to_int`` / ``self._diag`` / ``self._pid_cache_hit``
/ ``self._refresh_cookie_requirement`` / ``self._set_requires_cookie_meta`` /
``self._passes_artwork_filters`` / ``self._rescrape_within_days``), so behaviour
is unchanged.
"""
from __future__ import annotations

import datetime
import re

from app.core.pixiv_thread_utils import normalize_pid


class _Step3CachePrefilterMixin:
    def _lookup_url_meta_entry(self, pid_key):
        """Read self.url_meta[pid_key] safely; returns ``None`` for any failure."""
        try:
            meta = self._get_meta(pid_key)
        except Exception:
            return None
        if not meta:
            return None
        return meta

    def _meta_has_usable_url_and_pages(self, meta):
        img_url = str(meta.get("img_url", "") or "").strip()
        if not img_url or img_url == "None":
            return False
        pagecount = self._to_int(meta.get("pagecount", 0), 0) or 0
        return pagecount > 0

    def _is_pid_cached_meta(self, pid):
        pid_key = normalize_pid(pid) or str(pid)
        meta = self._lookup_url_meta_entry(pid_key)
        if meta is None:
            return False, {}
        if not self._meta_has_usable_url_and_pages(meta):
            return False, meta
        return True, meta

    @staticmethod
    def _expand_img_url_to_pages(img_url, page_total):
        """Expand a meta img_url ('..._p.jpg', '..._p0.jpg', '..._ugoira0.zip') into
        N per-page URLs. Returns [] when the URL has no extension separator."""
        if not img_url or "." not in img_url:
            return []
        if page_total < 1:
            page_total = 1
        left, right = img_url.rsplit(".", 1)
        # Normalise trailing "_pN" / "_ugoiraN" → "_p" / "_ugoira" so we can append idx.
        left_norm = re.sub(r"_(p|ugoira)\d+$", r"_\1", left, flags=re.IGNORECASE)
        if re.search(r"_(p|ugoira)$", left_norm, flags=re.IGNORECASE):
            return [left_norm + str(idx) + "." + right for idx in range(page_total)]
        # Fallback to legacy behavior (suffix on raw stem).
        return [left + str(idx) + "." + right for idx in range(page_total)]

    def _build_cached_urls_from_meta(self, pid, meta):
        pid_key = normalize_pid(pid) or str(pid)
        try:
            img_url = str((meta or {}).get("img_url", "") or "").strip()
            page_total = self._to_int((meta or {}).get("pagecount", 1), 1) or 1
            return self._expand_img_url_to_pages(img_url, page_total)
        except Exception:
            try:
                self._diag("step3_cached_url_build_failed", pid=str(pid_key))
            except Exception:
                pass
            return []

    def _refresh_cookie_requirement_for_cached(self, pid_key, meta):
        """Re-check requires_cookie for a cache-hit PID and stamp it on url_meta."""
        try:
            need_cookie = self._refresh_cookie_requirement(
                pid_key,
                fallback=(meta.get("requires_cookie") if isinstance(meta, dict) else None),
            )
        except Exception:
            need_cookie = None
        try:
            if isinstance(self.url_meta.get(pid_key), dict):
                self._set_requires_cookie_meta(pid_key, need_cookie)
        except Exception:
            pass
        return need_cookie

    def _record_cached_filter_decision(self, pid_key, passed, reason):
        """Stamp the artwork-filter outcome on the meta entry."""
        try:
            if isinstance(self.url_meta.get(pid_key), dict):
                self.url_meta[pid_key]["filter_pass"] = bool(passed)
                self.url_meta[pid_key]["filter_reason"] = str(reason)
        except Exception:
            pass

    def _prefilter_one_pid_with_cache(self, pid_key, stats):
        """Try to satisfy one PID from cache. Returns ``(cached_urls, needs_network)``."""
        has_cache, meta = self._is_pid_cached_meta(pid_key)
        if not has_cache:
            self._pid_cache_hit[pid_key] = False
            return [], True

        self._pid_cache_hit[pid_key] = True
        self._refresh_cookie_requirement_for_cached(pid_key, meta)

        tag = meta.get("tag", []) if isinstance(meta, dict) else []
        like = meta.get("like", 0) if isinstance(meta, dict) else 0
        passed, reason = self._passes_artwork_filters(pid_key, tag, like)
        self._record_cached_filter_decision(pid_key, passed, reason)
        if not passed:
            stats["cached_filtered"] += 1
            return [], False

        one_pid_urls = self._build_cached_urls_from_meta(pid_key, meta)
        if not one_pid_urls:
            self._pid_cache_hit[pid_key] = False
            stats["cached_fallback_network"] += 1
            return [], True

        stats["cached_hit_pid"] += 1
        stats["cached_generated_url"] += len(one_pid_urls)
        return one_pid_urls, False

    def _prefilter_step3_with_cache(self, pending_pids):
        network_pids = []
        cached_urls = []
        stats = {
            "cached_hit_pid": 0,
            "cached_generated_url": 0,
            "cached_filtered": 0,
            "cached_fallback_network": 0,
        }
        for raw_pid in pending_pids:
            pid_key = normalize_pid(raw_pid) or str(raw_pid)
            urls, needs_network = self._prefilter_one_pid_with_cache(pid_key, stats)
            if urls:
                cached_urls.extend(urls)
            if needs_network:
                network_pids.append(pid_key)
        return network_pids, cached_urls, stats

    @staticmethod
    def _step3_cache_is_usable(cached):
        """A cache entry counts as usable when img_url is non-empty AND pagecount > 0."""
        if not isinstance(cached, dict):
            return False
        if cached.get('img_url') in (None, 'None', ''):
            return False
        try:
            return int(cached.get('pagecount', 0) or 0) > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _coerce_rescrape_days(raw):
        """Coerce settings.json's ``rescrape_within_days`` into a non-negative int.

        Negative / non-numeric / None all collapse to 0 (feature disabled).
        """
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return 0
        return n if n > 0 else 0

    @staticmethod
    def _parse_pixiv_upload_date(value):
        """Parse Pixiv's ISO-8601 uploadDate string into a tz-aware datetime.

        Returns None for non-string / empty / unparseable input. Pixiv emits
        e.g. ``'2024-01-15T12:30:00+09:00'``. ``fromisoformat`` accepts that
        on Python >= 3.7, but rejects some valid ISO-8601 variants on Python
        3.10 (e.g. ``Z`` suffix, ``+0900`` without colon). The strptime
        fallback covers those cases without depending on a 3.11+ runtime.
        Naive inputs are treated as UTC defensively.
        """
        if not isinstance(value, str) or not value.strip():
            return None
        s = value.strip()
        try:
            dt = datetime.datetime.fromisoformat(s)
        except ValueError:
            dt = None
        if dt is None:
            # Normalize trailing 'Z' (Zulu / UTC) and tolerate offsets without
            # the colon, then retry via strptime.
            normalized = s
            if normalized.endswith('Z'):
                normalized = normalized[:-1] + '+0000'
            for fmt in (
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S",
            ):
                try:
                    dt = datetime.datetime.strptime(normalized, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt

    def _is_within_rescrape_window(self, cached):
        """True iff the cached entry was uploaded within ``self._rescrape_within_days`` days.

        Returns False (no rescrape) when:
          - the feature is disabled (threshold <= 0)
          - ``cached`` is not a dict
          - ``upload_date`` is missing or unparseable
          - the artwork is older than the threshold
          - the parsed upload_date is in the future (clock skew safeguard)
        """
        threshold = getattr(self, '_rescrape_within_days', 0)
        if threshold <= 0:
            return False
        if not isinstance(cached, dict):
            return False
        dt = self._parse_pixiv_upload_date(cached.get('upload_date'))
        if dt is None:
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        diff_days = (now - dt).total_seconds() / 86400.0
        return 0.0 <= diff_days < float(threshold)

    def _step3_cache_is_fresh(self, cached):
        """Cache short-circuits the network fetch only when both conditions hold:
           - the entry is well-formed (``_step3_cache_is_usable``); and
           - the artwork is NOT within the rescrape window.
        """
        if not self._step3_cache_is_usable(cached):
            return False
        if self._is_within_rescrape_window(cached):
            return False
        return True
