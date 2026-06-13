"""Bookmark-source scan path for ``get_pixiv_author_imgID_Thread`` (file-size refactor).

The 「收藏模式」 (bookmark-scope) PID source — paging a user's bookmarks via
the ``/ajax/user/{id}/illusts/bookmarks`` endpoint instead of following-list
artists. Pulled out of ``thread_pid_scan.py`` and mixed into the worker via
``_Step2BookmarkMixin``. Every method uses only ``self.`` for cross-method
calls (resolved through inheritance) plus the module-level names imported
below, so behavior is byte-for-byte identical to the originals. Helpers these
methods call but do not own (``_select_step2_cookie``,
``_record_step2_cookie_usage``, ``_acquire_account``,
``_run_with_network_retry``, ``_release_account_after_work``, ``_mark_pid_seen``,
``_emit_pid_write_error`` and the ``_collected_pids`` / ``_seen_pids`` state)
live on the concrete class or its base.
"""
from __future__ import annotations

import requests

from app.core.pixiv_thread_utils import normalize_pid
from app.core.worker_event import WorkerEvent


class _Step2BookmarkMixin:
    """Bookmark-source PID scan, mixed into ``get_pixiv_author_imgID_Thread``."""

    @staticmethod
    def _bookmark_rest_values(scope):
        if scope == "public":
            return ["show"]
        if scope == "private":
            return ["hide"]
        return ["show", "hide"]

    def _step2_fetch_bookmark_page(
        self, user_id, cookie, Agent, rest, offset, limit=48, proxies=None,
    ):
        url = (
            f"https://www.pixiv.net/ajax/user/{user_id}/illusts/bookmarks"
            f"?tag=&offset={int(offset)}&limit={int(limit)}&rest={rest}&lang=zh_tw"
        )
        headers = {
            "User-Agent": Agent,
            "Cookie": cookie,
            "referer": f"https://www.pixiv.net/users/{user_id}/bookmarks/artworks",
        }
        res = requests.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
        try:
            payload = res.json()
        except Exception:
            payload = {}
        body = payload.get("body", {}) if isinstance(payload, dict) else {}
        if not isinstance(body, dict):
            body = {}
        works = body.get("works", [])
        if not isinstance(works, list):
            works = []
        pids = []
        uid_map = {}
        for item in works:
            if not isinstance(item, dict):
                continue
            raw_pid = item.get("id") or item.get("illustId") or item.get("illust_id")
            pid = normalize_pid(raw_pid)
            if not pid:
                continue
            pids.append(pid)
            raw_uid = item.get("userId") or item.get("user_id")
            if raw_uid not in (None, ""):
                uid_map[pid] = str(raw_uid)
        try:
            total = int(body.get("total", len(pids)) or 0)
        except (TypeError, ValueError):
            total = len(pids)
        return pids, total, uid_map

    def _run_bookmarks_with_cookie(self, rest, offset, limit=48):
        cookie = self._select_step2_cookie()
        label_key = f"bookmarks:{rest}:{offset}"
        self._record_step2_cookie_usage(label_key, cookie)
        return self._step2_fetch_bookmark_page(
            self.bookmark_user_id,
            cookie,
            self.Agent,
            rest,
            offset,
            limit=limit,
        )

    def _run_bookmarks_with_acquired_cookie(self, rest, offset, limit=48):
        acc = self._acquire_account()
        if acc is None:
            return None
        label_key = f"bookmarks:{rest}:{offset}"
        self._record_step2_cookie_usage(label_key, acc.cookie)
        ok = False
        neutral = False
        try:
            ok, result, _ = self._run_with_network_retry(
                f"收藏 {rest} offset {offset}",
                lambda: self._step2_fetch_bookmark_page(
                    self.bookmark_user_id,
                    acc.cookie,
                    self.Agent,
                    rest,
                    offset,
                    limit=limit,
                    proxies=acc.proxies,
                ),
            )
            return result
        except Exception:
            # ReadTimeout / SSLError / ChunkedEncodingError etc. escape the
            # network-triple retry; release neutral and never leak held=True.
            neutral = True
            raise
        finally:
            self._release_account_after_work(acc, ok=ok, neutral=neutral)

    def _append_bookmark_pids(self, pids, uid_map):
        if not pids:
            return []
        kept = []
        skipped = []
        for raw_pid in pids:
            pid = normalize_pid(raw_pid)
            if not pid:
                continue
            if pid in self.exist_pid:
                skipped.append(pid)
                continue
            kept.append(pid)
        try:
            with self._collected_pids_lock:
                for pid in kept:
                    if pid in self._seen_pids:
                        continue
                    uid = uid_map.get(pid)
                    self._collected_pids.append((pid, None if uid in (None, "") else str(uid)))
                    self._mark_pid_seen(pid)
        except Exception as e:
            self._emit_pid_write_error(e)
        return kept

    def _execute_bookmark_tasks(self):
        limit = 48
        rest_values = self._bookmark_rest_values(self.bookmark_scope)
        results = []
        discovered = 0
        try:
            scope_label = {
                "public": "公開",
                "private": "非公開",
                "all": "全部",
            }.get(self.bookmark_scope, "全部")
            self._q.put(WorkerEvent(
                "output",
                f"<p><font color='blue'>收藏模式：抓取{scope_label}收藏作品 PID</font></p>",
            ))
        except Exception:
            pass
        for rest in rest_values:
            offset = 0
            total = None
            while not self._stop_event.is_set():
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break
                try:
                    if self._scheduler is not None:
                        page = self._run_bookmarks_with_acquired_cookie(rest, offset, limit=limit)
                    else:
                        page = self._run_bookmarks_with_cookie(rest, offset, limit=limit)
                    if page is None:
                        break
                    pids, page_total, uid_map = page
                    total = int(page_total or 0)
                    kept = self._append_bookmark_pids(pids, uid_map)
                    results.append(kept)
                    discovered += len(pids)
                    self._q.put(WorkerEvent("progress", (len(pids), max(total, offset + len(pids)))))
                    if len(pids) < limit:
                        break
                    offset += limit
                    if total and offset >= total:
                        break
                except Exception as e:
                    try:
                        self._q.put(WorkerEvent(
                            "output",
                            f"<p><font color='red'>收藏 {rest} offset {offset} 取得 PID 失敗：{e}</font></p>",
                        ))
                    except Exception:
                        pass
                    break
        try:
            self._q.put(WorkerEvent(
                "output",
                f"<p><font color='gray'>收藏 PID 掃描完成：讀取 {discovered} 筆</font></p>",
            ))
        except Exception:
            pass
        return results
