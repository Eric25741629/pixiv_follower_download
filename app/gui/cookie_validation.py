"""Cookie validation + auth-persistence subsystem for ``RunController``
(file-size refactor).

The pre-run cookie network test, the trust-cache freshness predicate, the
per-cookie status/timestamp write-back into ``settings.json.auth`` and the
alias attachment — mixed into ``RunController`` via ``_CookieValidationMixin``.
Every method uses only ``self.`` for cross-method calls (resolved through
inheritance: ``self._log`` / ``self._event_q``) plus the module-level names
below, so behavior is byte-for-byte identical to the originals.

``_store`` (the SettingsStore factory) is imported lazily inside the methods
that persist — it lives in ``app.gui.run_actions`` and a module-level import
here would form a cycle (run_actions imports this mixin). The function-local
import resolves at call time, after both modules are loaded, so it is
cycle-safe. ``_RETEST_INTERVAL_SEC`` lives here (the cookie logic owns it) and
is re-exported by ``run_actions`` for the tests that import it from there.
"""
from __future__ import annotations

from app.core.pixiv_thread_utils import normalize_cookie_entries
from app.core.worker_event import WorkerEvent

# Trust a "有效" cookie status without re-testing if it was checked within
# this window. Cookies that hit a runtime error (proxy_dead) get marked
# 失效 in settings, so the cache invalidates itself when something breaks.
_RETEST_INTERVAL_SEC = 30 * 86400  # 30 days


class _CookieValidationMixin:
    """Cookie test/validation + auth persistence, mixed into ``RunController``."""

    def _extract_cookie_entries(self, auth: dict) -> list[dict]:
        """Pull configured cookie entries (cookie + alias + status +
        last_tested_at) from auth settings, normalising whatever shape
        the user has saved.

        Entries explicitly disabled (``enabled is False``) are dropped
        from the result so disabled accounts never reach the validator
        or scheduler, and a gray skip notice is logged for each one.
        """
        raw = auth.get("cookies_entries") or auth.get("cookies_pool") or []
        if not raw:
            single = str(auth.get("cookies", "") or "").strip()
            if single:
                raw = [single]
        alias_map = auth.get("cookies_aliases") or {}
        if not isinstance(alias_map, dict):
            alias_map = {}
        entries = normalize_cookie_entries(raw, alias_map=alias_map)
        kept: list[dict] = []
        for e in entries:
            if e.get("enabled") is False:
                alias = (e.get("alias") or "").strip() or "Cookie"
                self._log(
                    f"<p><font color='gray'>Cookie「{alias}」已停用，本次跳過</font></p>"
                )
                continue
            kept.append(e)
        return kept

    @staticmethod
    def _cookie_cache_is_fresh(entry, now):
        """Return True iff this entry has status=有效 and was tested within the retest window."""
        if entry.get("status") != "有效":
            return False
        tested_at = entry.get("last_tested_at")
        try:
            tested_f = float(tested_at) if tested_at is not None else None
        except (TypeError, ValueError):
            return False
        if tested_f is None:
            return False
        return (now - tested_f) < _RETEST_INTERVAL_SEC

    def _partition_cookies_by_cache(self, entries, now):
        """Split entries into (cached_valid_cookies, entries_needing_network_test).

        Empty cookie strings are dropped entirely. For each cached-valid hit a
        gray "信任快取" log line is emitted with the staleness in days.
        """
        valid: list[str] = []
        needs_test: list[dict] = []
        for e in entries:
            cookie = str(e.get("cookie", "") or "").strip()
            if not cookie:
                continue
            if self._cookie_cache_is_fresh(e, now):
                valid.append(cookie)
                tested_f = float(e.get("last_tested_at"))
                days = max(0, int((now - tested_f) / 86400))
                alias = e.get("alias", "") or "Cookie"
                self._log(
                    f"<p><font color='gray'>{alias} 信任快取（{days} 天前驗證）</font></p>"
                )
            else:
                needs_test.append(e)
        return valid, needs_test

    def _run_one_cookie_test(self, cookie, idx, total, agent, pixiv_api_module):
        """Run a single cookie network test, emit progress + status events, return ok."""
        self._event_q.put(WorkerEvent(
            "loading", (True, f"測試 Cookie {idx}/{total}...")
        ))
        self._event_q.put(WorkerEvent("cookie_status", (cookie, "測試中", None)))
        try:
            count, _ = pixiv_api_module.Test_cookies([cookie], agent)
            ok = int(count) > 0
        except Exception:
            ok = False
        return ok

    def _test_cookies(self, entries: list[dict], agent: str) -> list[str]:
        """Validate each cookie, returning the valid cookie strings.

        Skips the network test for entries whose cached status is 有效
        and was checked within ``_RETEST_INTERVAL_SEC`` (30 days). For
        anything else (失效 / 未知 / stale / no timestamp), runs
        ``Test_cookies`` and persists the result.

        Pushes cookie_status events to the cookies view so the table
        reflects the newest state live, and writes status +
        last_tested_at back to settings."""
        if not entries:
            return []
        import time as _time
        from app.core import pixiv_api

        now = _time.time()
        valid, needs_test = self._partition_cookies_by_cache(entries, now)
        if not needs_test:
            return valid

        total = len(needs_test)
        self._log(f"<p><font color='blue'>啟動前測試 {total} 個 Cookie...</font></p>")
        tested_results: dict[str, bool] = {}
        for idx, e in enumerate(needs_test, start=1):
            cookie = str(e.get("cookie", "") or "").strip()
            ok = self._run_one_cookie_test(cookie, idx, total, agent, pixiv_api)
            tested_results[cookie] = ok
            status = "有效" if ok else "失效"
            self._event_q.put(WorkerEvent("cookie_status", (cookie, status, _time.time())))
            if ok:
                valid.append(cookie)
                self._log(f"<p><font color='green'>Cookie {idx}/{total} 有效</font></p>")
            else:
                self._log(
                    f"<p><font color='red'>Cookie {idx}/{total} 失效，已從本次任務排除</font></p>"
                )
        self._persist_cookie_statuses(tested_results, _time.time())
        return valid

    def _persist_cookie_statuses(
        self, tested_results: dict[str, bool], tested_at: float,
    ) -> None:
        """Write the latest test results (cookie -> ok bool) and shared
        timestamp back to cookies_entries[].status / last_tested_at so
        the cookies view reflects them on next reload. Untested entries
        keep their existing status untouched."""
        if not tested_results:
            return

        def _m(auth):
            entries = auth.get("cookies_entries") or []
            if not isinstance(entries, list):
                return auth
            new_entries = []
            for e in entries:
                if not isinstance(e, dict):
                    new_entries.append(e)
                    continue
                c = str(e.get("cookie", "")).strip()
                if c in tested_results:
                    new_entries.append({
                        **e,
                        "status": "有效" if tested_results[c] else "失效",
                        "last_tested_at": tested_at,
                    })
                else:
                    new_entries.append(e)
            return {**auth, "cookies_entries": new_entries}

        try:
            # mutate_section keeps load+write in one held lock so concurrent
            # cookie-status writers can't clobber each other (the old
            # get_section()/update_section() pair dropped the lock between).
            from app.gui.run_actions import _store
            _store().mutate_section("auth", _m)
        except Exception:
            pass

    def _refresh_cookie_timestamp(self, cookie: str) -> None:
        """Extend the trust-cache window for a cookie that just had its
        first successful network request this run.  Only touches
        last_tested_at — never changes status — so a subsequent
        _invalidate_cookie_status() call still overwrites with 失效."""
        if not cookie:
            return
        import time as _time
        now = _time.time()
        refreshed = {"at": None}

        def _m(auth):
            entries = auth.get("cookies_entries") or []
            if not isinstance(entries, list):
                return auth
            new_entries = []
            for e in entries:
                if not isinstance(e, dict):
                    new_entries.append(e)
                    continue
                c = str(e.get("cookie", "")).strip()
                if c == cookie.strip() and e.get("status") == "有效":
                    refreshed["at"] = now
                    new_entries.append({**e, "last_tested_at": now})
                else:
                    new_entries.append(e)
            return {**auth, "cookies_entries": new_entries}

        try:
            from app.gui.run_actions import _store
            _store().mutate_section("auth", _m)
            if refreshed["at"] is not None:
                self._event_q.put(WorkerEvent(
                    "cookie_status", (cookie.strip(), "有效", refreshed["at"])
                ))
        except Exception:
            pass

    def _invalidate_cookie_status(self, cookie: str) -> None:
        """Mark a single cookie as 失效 in settings (called from the
        scheduler's on_disable callback when proxy/auth fails at
        runtime). Sets last_tested_at=now so the cache is treated as
        fresh-but-bad — next run re-tests instead of trusting it."""
        if not cookie:
            return
        import time as _time
        now = _time.time()

        def _m(auth):
            entries = auth.get("cookies_entries") or []
            if not isinstance(entries, list):
                return auth
            new_entries = []
            for e in entries:
                if not isinstance(e, dict):
                    new_entries.append(e)
                    continue
                c = str(e.get("cookie", "")).strip()
                if c == cookie.strip():
                    new_entries.append({**e, "status": "失效", "last_tested_at": now})
                else:
                    new_entries.append(e)
            return {**auth, "cookies_entries": new_entries}

        try:
            from app.gui.run_actions import _store
            _store().mutate_section("auth", _m)
            # Mirror _refresh_cookie_timestamp: push a live cookie_status event
            # so a cookie disabled mid-run flips to 失效 in the cookies view
            # immediately, not only after the next reload_from_settings.
            self._event_q.put(
                WorkerEvent("cookie_status", (cookie.strip(), "失效", now))
            )
        except Exception:
            pass

    def _attach_aliases(
        self, valid_cookies: list[str], auth: dict,
    ) -> list[dict]:
        """Pair each validated cookie with its alias from
        ``auth.cookies_aliases`` so worker threads can resolve aliases
        for log lines and stats (otherwise ``cookie_usage_label`` falls
        back to ``Cookie{n}``)."""
        alias_map = auth.get("cookies_aliases") or {}
        if not isinstance(alias_map, dict):
            alias_map = {}
        return [
            {"cookie": c, "alias": str(alias_map.get(c, "") or "").strip()}
            for c in valid_cookies
        ]

    def _validate_cookies_for_step(self, auth, agent, step_num):
        """Test cookies and return the valid list, or None on failure (with log)."""
        cookie_entries = self._extract_cookie_entries(auth)
        valid_cookies = self._test_cookies(cookie_entries, agent)
        if not valid_cookies:
            self._log(
                f"<p><font color='red'>所有 Cookie 都無效，無法啟動步驟 {step_num}</font></p>"
            )
            return None
        return valid_cookies
