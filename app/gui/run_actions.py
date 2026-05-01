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
from typing import Optional

from app.core.settings_store import SettingsStore
from app.core.worker_event import WorkerEvent
from app.core.account_scheduler import AccountState, AccountScheduler
from app.core.proxy_utils import parse_proxy_url
from app.core.pixiv_thread_utils import safe_read_json, load_exist_pid_set
from app.core import thread_following, thread_pid_scan, thread_url_fetch, thread_download

DEFAULT_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)


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

    def run_step(self, n: int) -> None:
        self._run_all_mode = False
        self._start_step(n)

    def run_all(self) -> None:
        self._run_all_mode = True
        self._start_step(1)

    def on_next(self, n: int) -> None:
        if n == -1 or not self._run_all_mode:
            return
        if 1 <= n <= 4:
            self._start_step(n)

    def _log(self, html: str) -> None:
        self._event_q.put(WorkerEvent("output", html))

    def _extract_cookies_list(self, auth: dict) -> list[str]:
        """Pull the configured cookie strings out of auth settings,
        preferring cookies_entries -> cookies_pool -> single cookies."""
        entries = auth.get("cookies_entries") or []
        pool = auth.get("cookies_pool") or []
        if isinstance(entries, list) and entries:
            cookies_list = [
                e.get("cookie", "") if isinstance(e, dict) else str(e)
                for e in entries
            ]
        elif isinstance(pool, list) and pool:
            cookies_list = [str(c) for c in pool]
        else:
            raw = str(auth.get("cookies", "") or "")
            cookies_list = [raw] if raw.strip() else []
        return [c.strip() for c in cookies_list if c and c.strip()]

    def _test_cookies(self, cookies_list: list[str], agent: str) -> list[str]:
        """Validate each cookie via Test_cookies and return the valid ones.

        Updates the loading-dialog message per cookie, pushes
        cookie_status events so the cookies view reflects results live,
        and persists the new statuses back to settings."""
        if not cookies_list:
            return []
        from app.core import pixiv_api
        valid: list[str] = []
        total = len(cookies_list)
        self._log(f"<p><font color='blue'>啟動前測試 {total} 個 Cookie...</font></p>")
        for idx, cookie in enumerate(cookies_list, start=1):
            self._event_q.put(WorkerEvent(
                "loading", (True, f"測試 Cookie {idx}/{total}...")
            ))
            self._event_q.put(WorkerEvent("cookie_status", (cookie, "測試中")))
            try:
                count, _ = pixiv_api.Test_cookies([cookie], agent)
                ok = int(count) > 0
            except Exception:
                ok = False
            status = "有效" if ok else "失效"
            self._event_q.put(WorkerEvent("cookie_status", (cookie, status)))
            if ok:
                valid.append(cookie)
                self._log(f"<p><font color='green'>Cookie {idx}/{total} 有效</font></p>")
            else:
                self._log(
                    f"<p><font color='red'>Cookie {idx}/{total} 失效，已從本次任務排除</font></p>"
                )
        self._persist_cookie_statuses(cookies_list, valid)
        return valid

    def _persist_cookie_statuses(
        self, tested_cookies: list[str], valid: list[str]
    ) -> None:
        """Write the latest test result back to cookies_entries[].status
        so the cookies view reflects them on next reload."""
        valid_set = set(valid)
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
                if c in tested_cookies:
                    new_entries.append({**e, "status": "有效" if c in valid_set else "失效"})
                else:
                    new_entries.append(e)
            store.update_section("auth", {**auth, "cookies_entries": new_entries})
        except Exception:
            pass

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
            userid = str(auth.get("userid", "")).strip()
            if not userid:
                self._log("<p><font color='red'>請先在「設定」填入 User ID</font></p>")
                return None
            cookies_list = self._extract_cookies_list(auth)
            valid_cookies = self._test_cookies(cookies_list, agent)
            if not valid_cookies:
                self._log("<p><font color='red'>所有 Cookie 都無效，無法啟動步驟 1</font></p>")
                return None
            return thread_following.get_following(
                self._event_q,
                userid,
                valid_cookies[0],
                agent,
                bool(flt.get("hidefollow", False)),
            )

        if n == 2:
            authors = _load_author_list()
            if not authors:
                self._log("<p><font color='red'>找不到 following 清單，請先執行步驟 1</font></p>")
                return None
            cookies_list = self._extract_cookies_list(auth)
            valid_cookies = self._test_cookies(cookies_list, agent)
            if not valid_cookies:
                self._log("<p><font color='red'>所有 Cookie 都無效，無法啟動步驟 2</font></p>")
                return None
            t = thread_pid_scan.get_pixiv_author_imgID_Thread(
                self._event_q,
                authors,
                agent,
                path,
                valid_cookies,
                load_exist_pid_set(path),
                bool(perf.get("single_thread_mode", False)),
            )
            t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
            return t

        if n == 3:
            authors = _load_author_list()
            cookies_list = self._extract_cookies_list(auth)
            valid_cookies = self._test_cookies(cookies_list, agent)
            if not valid_cookies:
                self._log("<p><font color='red'>所有 Cookie 都無效，無法啟動步驟 3</font></p>")
                return None
            t = thread_url_fetch.get_img_url_thread(
                q=self._event_q,
                Author_list=authors,
                Agent=agent,
                cookies=valid_cookies,
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
            )
            t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
            return t

        if n == 4:
            dl_path = str(dl.get("path", "")).strip()
            if not dl_path:
                self._log("<p><font color='red'>請先在「設定」指定下載路徑</font></p>")
                return None
            dt_str = str(dl.get("download_time", "")).strip()
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S") if dt_str else datetime(1970, 1, 1)
            except Exception:
                dt = datetime(1970, 1, 1)
            cookies_list = self._extract_cookies_list(auth)
            valid_cookies = self._test_cookies(cookies_list, agent)
            if not valid_cookies:
                self._log("<p><font color='red'>所有 Cookie 都無效，無法啟動步驟 4</font></p>")
                return None
            t = thread_download.download_thread(
                q=self._event_q,
                nogif=bool(flt.get("nogif", False)),
                notag=bool(flt.get("notag", False)),
                notime=bool(flt.get("notime", False)),
                create_dir=bool(directory.get("create_dir", False)),
                download_path=dl_path,
                cookies=valid_cookies,
                agent=agent,
                download_time=dt,
                no_R18G_dir=bool(directory.get("no_R18G_dir", False)),
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
                stats_collector=self._stats_collector,
            )
            t._scheduler = self._build_scheduler(auth, valid_cookies, t._pause_event, t._stop_event)
            return t
        return None
