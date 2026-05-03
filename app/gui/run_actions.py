"""Step orchestration for the Flet GUI.

Reads settings from SettingsStore (no Qt UI access), constructs the four
worker threads with proper args, and exposes a small RunController that
the views layer drives via button callbacks. Chained "Run All" mode is
implemented by reacting to WorkerEvent("next", N) inside on_next().
"""
from __future__ import annotations
import os
from datetime import datetime
from queue import Queue

from app.core.settings_store import SettingsStore
from app.core.worker_event import WorkerEvent
from app.core.account_scheduler import AccountState, AccountScheduler
from app.core.proxy_utils import parse_proxy_url
from app.core.pixiv_thread_utils import (
    safe_read_json, load_exist_pid_set, normalize_cookie_entries, backup_file,
)
from app.core.metadata_db import DB_FILENAME
from app.core import thread_following, thread_pid_scan, thread_url_fetch, thread_download

DEFAULT_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)

# Trust a "有效" cookie status without re-testing if it was checked within
# this window. Cookies that hit a runtime error (proxy_dead) get marked
# 失效 in settings, so the cache invalidates itself when something breaks.
_RETEST_INTERVAL_SEC = 30 * 86400  # 30 days


def _data_path() -> str:
    p = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(p, exist_ok=True)
    return p


def _store() -> SettingsStore:
    s = SettingsStore(_data_path())
    s.migrate_from_legacy()
    return s


def _agent(auth: dict) -> str:
    return str(auth.get("agent") or "").strip() or DEFAULT_AGENT


def _load_author_list() -> list[str]:
    path = _data_path()
    j = safe_read_json(os.path.join(path, "following.json"), None)
    if isinstance(j, list) and j:
        return [str(x) for x in j]
    txt_path = os.path.join(path, "following.txt")
    if os.path.isfile(txt_path):
        with open(txt_path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return []


class RunController:
    """Wires step buttons to worker threads and chains them in Run-All mode."""

    def __init__(self, main_view, event_q: Queue, stats_collector=None):
        self._main_view = main_view
        self._event_q = event_q
        self._run_all_mode = False
        self._stats_collector = stats_collector

    def _backup_db(self) -> None:
        try:
            db_path = os.path.join(_data_path(), DB_FILENAME)
            if not os.path.isfile(db_path):
                return
            from app.core.metadata_db import MetadataDB
            MetadataDB(_data_path()).backup_db(max_history=3)
        except Exception:
            pass

    def run_step(self, n: int) -> None:
        self._run_all_mode = False
        self._backup_db()
        self._start_step(n)

    def run_all(self) -> None:
        self._run_all_mode = True
        self._backup_db()
        self._start_step(1)

    def on_next(self, n: int) -> None:
        if n == -1 or not self._run_all_mode:
            return
        if 1 <= n <= 4:
            self._start_step(n)

    def _log(self, html: str) -> None:
        self._event_q.put(WorkerEvent("output", html))

    def _extract_cookie_entries(self, auth: dict) -> list[dict]:
        """Pull configured cookie entries (cookie + alias + status +
        last_tested_at) from auth settings, normalising whatever shape
        the user has saved."""
        raw = auth.get("cookies_entries") or auth.get("cookies_pool") or []
        if not raw:
            single = str(auth.get("cookies", "") or "").strip()
            if single:
                raw = [single]
        alias_map = auth.get("cookies_aliases") or {}
        if not isinstance(alias_map, dict):
            alias_map = {}
        return normalize_cookie_entries(raw, alias_map=alias_map)

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
        try:
            store = _store()
            auth = store.get_section("auth")
            entries = auth.get("cookies_entries") or []
            if not isinstance(entries, list):
                return
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
            store.update_section("auth", {**auth, "cookies_entries": new_entries})
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
        try:
            store = _store()
            auth = store.get_section("auth")
            entries = auth.get("cookies_entries") or []
            if not isinstance(entries, list):
                return
            now = _time.time()
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
            store.update_section("auth", {**auth, "cookies_entries": new_entries})
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

    def _build_scheduler(
        self,
        auth: dict,
        valid_cookies: list[str],
        pause_event,
        stop_event,
    ) -> AccountScheduler:
        """Build an AccountScheduler from a pre-validated cookie list.

        Alias is sourced from auth.cookies_aliases, proxy from
        auth.cookie_proxy_map. The scheduler reads pid_cooldown_avg from
        settings on every release(), so live slider edits in the settings
        UI take effect without restart.
        """
        alias_map = auth.get("cookies_aliases") or {}
        proxy_map = auth.get("cookie_proxy_map") or {}
        accounts: list[AccountState] = []
        for i, cookie in enumerate(valid_cookies):
            alias = alias_map.get(cookie) or f"Cookie {i + 1}"
            raw_proxy = proxy_map.get(cookie) or None
            proxy_url = parse_proxy_url(raw_proxy) if raw_proxy else None
            accounts.append(AccountState(cookie=cookie, alias=alias, proxy_url=proxy_url))

        return AccountScheduler(
            accounts=accounts,
            get_cooldown_avg=lambda: float(
                _store().get_section("performance").get("pid_cooldown_avg", 35)
            ),
            pause_event=pause_event,
            stop_event=stop_event,
            emit=self._log,
            q=self._event_q,
            on_disable=lambda acc: self._invalidate_cookie_status(acc.cookie),
        )

    def _start_step(self, n: int) -> None:
        try:
            t = self._build_thread(n)
        except Exception as err:
            self._log(f"<p><font color='red'>步驟 {n} 建立執行緒失敗：{err}</font></p>")
            self._main_view.set_running(False)
            self._main_view.set_step_state(n - 1, "error")
            return
        if t is None:
            self._main_view.set_running(False)
            return
        self._main_view._active_thread = t
        self._main_view.set_running(True)
        for i in range(4):
            self._main_view.set_step_state(i, "idle")
        self._main_view.set_step_state(n - 1, "running")
        self._log(f"<p><font color='gray'>--- 步驟 {n} 開始 ---</font></p>")
        t.start()

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

    def _build_step1(self, auth, agent, flt):
        userid = str(auth.get("userid", "")).strip()
        if not userid:
            self._log("<p><font color='red'>請先在「設定」填入 User ID</font></p>")
            return None
        valid_cookies = self._validate_cookies_for_step(auth, agent, 1)
        if not valid_cookies:
            return None
        return thread_following.get_following(
            self._event_q,
            userid,
            valid_cookies[0],
            agent,
            bool(flt.get("hidefollow", False)),
        )

    def _build_step2(self, auth, agent, perf, path):
        authors = _load_author_list()
        if not authors:
            self._log(
                "<p><font color='red'>找不到 following 清單，請先執行步驟 1</font></p>"
            )
            return None
        valid_cookies = self._validate_cookies_for_step(auth, agent, 2)
        if not valid_cookies:
            return None
        t = thread_pid_scan.get_pixiv_author_imgID_Thread(
            self._event_q,
            authors,
            agent,
            path,
            self._attach_aliases(valid_cookies, auth),
            load_exist_pid_set(path),
            bool(perf.get("single_thread_mode", False)),
            stats_collector=self._stats_collector,
        )
        t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
        return t

    def _build_step3(self, auth, agent, dl, perf, path):
        authors = _load_author_list()
        valid_cookies = self._validate_cookies_for_step(auth, agent, 3)
        if not valid_cookies:
            return None
        t = thread_url_fetch.get_img_url_thread(
            q=self._event_q,
            Author_list=authors,
            Agent=agent,
            cookies=self._attach_aliases(valid_cookies, auth),
            exist_pid=load_exist_pid_set(path),
            ban_tag=list(dl.get("ban_tag", [])),
            must_tag=list(dl.get("must_tag", [])),
            like_num=int(dl.get("like_num", 0)),
            no_to_check=[],
            base_path=path,
            single_thread_mode=bool(perf.get("single_thread_mode", False)),
            pid_wait_nocookie_min=int(perf.get("pid_wait_nocookie_min", 1)),
            pid_wait_nocookie_max=int(perf.get("pid_wait_nocookie_max", 6)),
            special_like_rules=[],
            stats_collector=self._stats_collector,
        )
        t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
        return t

    @staticmethod
    def _parse_download_time(dt_str):
        """Parse download_time setting; falls back to epoch on any error."""
        s = str(dt_str or "").strip()
        if not s:
            return datetime(1970, 1, 1)
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime(1970, 1, 1)

    def _build_step4(self, auth, agent, dl, flt, perf, directory, jxl):
        dl_path = str(dl.get("path", "")).strip()
        if not dl_path:
            self._log("<p><font color='red'>請先在「設定」指定下載路徑</font></p>")
            return None
        dt = self._parse_download_time(dl.get("download_time"))
        valid_cookies = self._validate_cookies_for_step(auth, agent, 4)
        if not valid_cookies:
            return None
        t = thread_download.download_thread(
            q=self._event_q,
            nogif=bool(flt.get("nogif", False)),
            notag=bool(flt.get("notag", False)),
            notime=bool(flt.get("notime", False)),
            create_dir=bool(directory.get("create_dir", False)),
            download_path=dl_path,
            cookies=self._attach_aliases(valid_cookies, auth),
            agent=agent,
            download_time=dt,
            no_R18G_dir=bool(directory.get("no_R18G_dir", False)),
            no_R18_dir=bool(directory.get("no_R18_dir", False)),
            single_thread_mode=bool(perf.get("single_thread_mode", False)),
            intra_pid_wait_min=int(perf.get("pid_wait_nocookie_min", 1)),
            intra_pid_wait_max=int(perf.get("pid_wait_nocookie_max", 6)),
            jxl_enable=bool(jxl.get("enable", False)),
            jxl_cjxl_path=str(jxl.get("cjxl_path", "")),
            jxl_delete_original=bool(jxl.get("delete_original", False)),
            jxl_effort=int(jxl.get("effort", 7)),
            like_num=int(dl.get("like_num", 0)),
            ban_tag=list(dl.get("ban_tag", [])),
            must_tag=list(dl.get("must_tag", [])),
            special_like_rules=[],
            ai_gen_dir=bool(directory.get("ai_gen_dir", False)),
            filename_template=str(dl.get("filename_template", "") or ""),
            stats_collector=self._stats_collector,
        )
        t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
        return t

    def _build_thread(self, n: int):
        store = _store()
        auth = store.get_section("auth")
        dl = store.get_section("download")
        flt = store.get_section("filter")
        perf = store.get_section("performance")
        directory = store.get_section("directory")
        jxl = store.get_section("jxl")
        agent = _agent(auth)
        path = _data_path()

        if n == 1:
            return self._build_step1(auth, agent, flt)
        if n == 2:
            return self._build_step2(auth, agent, perf, path)
        if n == 3:
            return self._build_step3(auth, agent, dl, perf, path)
        if n == 4:
            return self._build_step4(auth, agent, dl, flt, perf, directory, jxl)
        return None
