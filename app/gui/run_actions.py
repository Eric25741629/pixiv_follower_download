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


def _cookie_payload(auth: dict):
    entries = auth.get("cookies_entries") or []
    if isinstance(entries, list) and entries:
        return entries
    pool = auth.get("cookies_pool") or []
    if isinstance(pool, list) and pool:
        return pool
    return str(auth.get("cookies", "") or "")


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

    def __init__(self, main_view, event_q: Queue):
        self._main_view = main_view
        self._event_q = event_q
        self._run_all_mode = False

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
        cookies = _cookie_payload(auth)
        path = _data_path()

        if n == 1:
            userid = str(auth.get("userid", "")).strip()
            if not userid:
                self._log("<p><font color='red'>請先在「設定」填入 User ID</font></p>")
                return None
            cookies_str = cookies if isinstance(cookies, str) else (str(auth.get("cookies", "")) or "")
            return thread_following.get_following(
                self._event_q,
                userid,
                cookies_str,
                agent,
                bool(flt.get("hidefollow", False)),
            )

        if n == 2:
            authors = _load_author_list()
            if not authors:
                self._log("<p><font color='red'>找不到 following 清單，請先執行步驟 1</font></p>")
                return None
            return thread_pid_scan.get_pixiv_author_imgID_Thread(
                self._event_q,
                authors,
                agent,
                path,
                cookies,
                load_exist_pid_set(path),
                bool(perf.get("single_thread_mode", False)),
                int(perf.get("pid_wait_min", 10)),
                int(perf.get("pid_wait_max", 60)),
            )

        if n == 3:
            authors = _load_author_list()
            return thread_url_fetch.get_img_url_thread(
                q=self._event_q,
                Author_list=authors,
                Agent=agent,
                cookies=cookies,
                exist_pid=load_exist_pid_set(path),
                ban_tag=list(dl.get("ban_tag", [])),
                must_tag=list(dl.get("must_tag", [])),
                like_num=int(dl.get("like_num", 0)),
                no_to_check=[],
                base_path=path,
                single_thread_mode=bool(perf.get("single_thread_mode", False)),
                pid_wait_min=int(perf.get("pid_wait_min", 10)),
                pid_wait_max=int(perf.get("pid_wait_max", 60)),
                pid_wait_nocookie_min=int(perf.get("pid_wait_nocookie_min", 1)),
                pid_wait_nocookie_max=int(perf.get("pid_wait_nocookie_max", 6)),
                special_like_rules=[],
            )

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
            return thread_download.download_thread(
                q=self._event_q,
                nogif=bool(flt.get("nogif", False)),
                notag=bool(flt.get("notag", False)),
                notime=bool(flt.get("notime", False)),
                create_dir=bool(directory.get("create_dir", False)),
                download_path=dl_path,
                cookies=cookies,
                agent=agent,
                download_time=dt,
                no_R18G_dir=bool(directory.get("no_R18G_dir", False)),
                single_thread_mode=bool(perf.get("single_thread_mode", False)),
                download_wait_min=int(perf.get("pid_wait_min", 10)),
                download_wait_max=int(perf.get("pid_wait_max", 60)),
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
            )
        return None
