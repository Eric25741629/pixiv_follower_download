"""Per-page media fetchers for ``download_thread`` (file-size refactor).

The ugoira (animated GIF) and still-image (jpg/png) download mechanics, mixed
into ``download_thread`` via ``_Step4MediaMixin``. Every method uses only
``self.`` for cross-method calls (resolved through inheritance) plus the
module-level names imported below, so behavior is byte-for-byte identical to
the originals. Sibling helpers these methods call (``_resolve_pid_and_cookie``,
``_load_artwork_metadata``, ``_build_artwork_headers``,
``_resolve_download_target_dir``, ``_build_hashtag_text``,
``_build_download_filename``, ``_get_meta``, ``_upsert_meta_in_db``,
``_enqueue_jxl``, ``_log_ugoira_meta_failure``) live on the concrete class or
its other mixins.
"""
from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import random as pyrandom
import time
import zipfile

import requests
from PIL import Image

from app.core.pixiv_thread_base import _cookie_usage_label
from app.core.pixiv_thread_utils import (
    atomic_write_json,
    fetch_with_cookie_retry,
    normalize_pid,
)
from app.core.worker_event import WorkerEvent


class _Step4MediaMixin:
    """Ugoira + jpg per-page download mechanics, mixed into ``download_thread``."""

    def _normalize_ugoira_frames(self, frame_blobs):
        loaded_frames = []
        max_width = 0
        max_height = 0

        for blob in frame_blobs:
            if not blob:
                continue
            try:
                with Image.open(io.BytesIO(blob)) as img:
                    rgba = img.convert("RGBA")
                frame = rgba.copy()
            except Exception:
                continue
            width, height = frame.size
            if width > max_width:
                max_width = width
            if height > max_height:
                max_height = height
            loaded_frames.append(frame)

        if not loaded_frames:
            raise ValueError("no valid ugoira frames to encode")

        normalized_frames = []
        target_size = (max_width, max_height)
        for frame in loaded_frames:
            if frame.size != target_size:
                canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
                canvas.paste(frame, (0, 0), frame)
                frame = canvas
            normalized_frames.append(frame.convert("P", palette=Image.ADAPTIVE))
        return normalized_frames

    def _save_ugoira_gif(self, frame_blobs, output_path, delay_info):
        frames = self._normalize_ugoira_frames(frame_blobs)
        frame_count = len(frames)

        durations = []
        if isinstance(delay_info, list) and delay_info:
            for value in delay_info[:frame_count]:
                try:
                    durations.append(max(1, int(value)))
                except Exception:
                    durations.append(100)
            if len(durations) < frame_count:
                pad_value = durations[-1] if durations else 100
                durations.extend([pad_value] * (frame_count - len(durations)))
        else:
            durations = [100] * frame_count

        frames[0].save(
            output_path,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            optimize=False,
            disposal=2,
        )

    def _stamp_step4_gif_cookie_usage(self, pid_key, source):
        """Mark requires_cookie + cookie_used on the in-memory url_meta entry and DB."""
        try:
            meta = dict(self._get_meta(pid_key))
            meta["requires_cookie"] = True
            meta["cookie_used"] = True
            meta["cookie_used_source"] = str(source)
            meta["cookie_used_updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.url_meta[pid_key] = meta
            self._upsert_meta_in_db(pid_key, meta)
        except Exception:
            pass

    def _atomic_write_url_meta_with_raw_fallback(self):
        """atomic_write_json with raw open()-fallback on failure."""
        try:
            atomic_write_json(self.url_meta_path, self.url_meta, backup=True)
        except Exception:
            try:
                with open(self.url_meta_path, 'w', encoding='utf-8') as f:
                    json.dump(self.url_meta, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    def _mark_gif_cookie_usage(self, pid, used, source="unknown"):
        pid_key = normalize_pid(pid) or str(pid)
        used_flag = bool(used)
        with contextlib.suppress(Exception):
            self._pid_cookie_used[pid_key] = used_flag
        if not used_flag:
            return

        # Hold _url_meta_lock so the read-modify-write of self.url_meta and
        # the JSON serialization-then-replace cannot race a concurrent worker.
        lock = getattr(self, "_url_meta_lock", None)
        if lock is not None:
            lock.acquire()
        try:
            self._stamp_step4_gif_cookie_usage(pid_key, source)
            self._atomic_write_url_meta_with_raw_fallback()
        finally:
            if lock is not None:
                lock.release()

        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output",
                f"<p><font color='blue'>[GIF][Cookie] PID {pid_key} 使用 cookies（來源：{source}），已更新 all_url_meta 暫存</font></p>"
            ))

    def _stream_ugoira_zip_bytes(self, url, headers, http, pid_cookie):
        """Fetch a ugoira zip URL and return the full bytes blob (or None on error).

        Advances ``self.download_time`` (under timelock) and reports stats
        when streaming succeeds, matching the original inline behavior.
        """
        try:
            resp = http.get(url, headers=headers, stream=True)
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError):
            raise
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        chunks = [data for data in resp.iter_content(chunk_size=65536) if data]
        zip_bytes = b"".join(chunks)
        if self._stats_collector is not None and zip_bytes:
            self._stats_collector.report_bytes(len(zip_bytes))
            label = _cookie_usage_label(pid_cookie, self.cookie_pool, self._cookie_alias_map)
            self._stats_collector.report_request(label)
        return zip_bytes or None

    def _extract_ugoira_frame_blobs(self, zip_bytes):
        """Return the per-frame byte blobs from a ugoira zip, in archive order."""
        frame_blobs = []
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zipo:
            for member in zipo.namelist():
                if member.endswith('/'):
                    continue
                frame_blobs.append(zipo.read(member))
        return frame_blobs

    def _build_ugoira_save_path(self, pid, tag, my_time):
        """Build the absolute save path for a ugoira GIF, with fallback on naming failure."""
        try:
            hashtag = self._build_hashtag_text(tag, max_len=230)
            name = self._build_download_filename(
                pid,
                page_suffix="",
                ext="gif",
                hashtag=hashtag,
                timetag=my_time.strftime('%Y%m%d_%H%M%S'),
                notag=self.notag,
                notime=self.notime,
                template=getattr(self, "filename_template", ""),
            )
        except Exception:
            name = 'illust_' + pid + my_time.strftime('_%Y%m%d_%H%M%S.gif')
        target_dir = self._resolve_download_target_dir(tag, pid, media_kind='GIF')
        return os.path.join(target_dir, name)

    def _diag_ugoira_meta_fetch(self, pid, meta_trace):
        """Append a step4 diagnostic record describing the ugoira meta fetch result."""
        with contextlib.suppress(Exception):
            self._diag(
                "ugoira_meta_fetch",
                pid=str(pid),
                first_try_status=meta_trace.get("first_try_status"),
                retry_used=bool(meta_trace.get("retry_used")),
                retry_with_cookie_status=meta_trace.get("retry_with_cookie_status"),
                final_status=meta_trace.get("final_status"),
            )

    def _maybe_mark_meta_retry_cookie(self, pid, meta_trace):
        """If the with-cookie retry succeeded, record cookie usage and return True."""
        try:
            retry_used = bool(meta_trace.get("retry_used"))
            retry_status = int(meta_trace.get("retry_with_cookie_status") or 0)
        except Exception:
            return False
        if retry_used and retry_status == 200:
            self._mark_gif_cookie_usage(pid, True, source="ugoira_meta_retry")
            return True
        return False

    @staticmethod
    def _parse_ugoira_meta_payload(htmlfile, pid):
        """Parse the ugoira_meta JSON. Returns (download_url, delay_info) or None on bad payload."""
        try:
            gif_info = json.loads(htmlfile.content)['body']
            download_url = gif_info['originalSrc']
            delay_info = [item["delay"] for item in gif_info["frames"]]
            return download_url, delay_info
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[pixiv_thread] PID {pid} JSON parse failed: {e}")
            print(f"[pixiv_thread] response preview: {htmlfile.text[:500]}")
            return None

    def _fetch_ugoira_meta(self, pid, pid_cookie, need_cookie, session):
        """Fetch ugoira_meta with cookie-retry. Returns (download_url, delay_info, need_cookie)
        on success, or ``None`` on any failure (404 / parse error / non-200)."""
        url = f'https://www.pixiv.net/ajax/illust/{pid}/ugoira_meta?lang=zh_tw'
        headers = self._build_artwork_headers(pid, pid_cookie, need_cookie)
        self._mark_gif_cookie_usage(
            pid,
            bool(need_cookie is True and pid_cookie),
            source="ugoira_meta_initial",
        )
        http = session if session is not None else requests
        htmlfile, meta_trace, first_try_resp = fetch_with_cookie_retry(
            http_get=http.get,
            url=url,
            headers=headers,
            cookies=pid_cookie,
            retry_statuses=(403, 404),
        )
        if self._maybe_mark_meta_retry_cookie(pid, meta_trace):
            need_cookie = True
        self._diag_ugoira_meta_fetch(pid, meta_trace)
        if htmlfile.status_code != 200:
            self._log_ugoira_meta_failure(pid, htmlfile, meta_trace, first_try_resp)
            return None
        htmlfile.raise_for_status()
        if self._stats_collector is not None:
            label = _cookie_usage_label(pid_cookie, self.cookie_pool, self._cookie_alias_map)
            self._stats_collector.report_request(label)
        parsed = self._parse_ugoira_meta_payload(htmlfile, pid)
        if parsed is None:
            return None
        download_url, delay_info = parsed
        return download_url, delay_info, need_cookie

    def gif_download(self, url, session=None):
        with self.timelock:
            my_time = self.download_time
        try:
            pid, pid_cookie, need_cookie = self._resolve_pid_and_cookie(url, source="step4")
            normalized = self._load_artwork_metadata(pid, pid_cookie)
            if not normalized:
                with contextlib.suppress(Exception):
                    self._q.put(WorkerEvent("output",
                        f"<p><font color='orange'>PID {pid} 取得 ugoira 資訊失敗，"
                        f"已標記為失敗任務</font></p>"))
                return [url, my_time.strftime('%Y%m%d_%H%M%S')]
            tag, like, pagecount, img_url = normalized
            meta = self._fetch_ugoira_meta(pid, pid_cookie, need_cookie, session)
            if meta is None:
                return None
            download_url, delay_info, need_cookie = meta
            url = download_url
            http = session if session is not None else requests
            headers = self._build_artwork_headers(pid, pid_cookie, need_cookie, honour_pid_used=True)
            with self.timelock:
                my_time = self.download_time
                self.download_time = self.download_time + datetime.timedelta(seconds=1)
            zip_bytes = self._stream_ugoira_zip_bytes(url, headers, http, pid_cookie)
            if not zip_bytes:
                return [url, my_time.strftime('%Y%m%d_%H%M%S')]
            frame_blobs = self._extract_ugoira_frame_blobs(zip_bytes)
            if not frame_blobs:
                return [url, my_time.strftime('%Y%m%d_%H%M%S')]
            saved_gif_path = self._build_ugoira_save_path(pid, tag, my_time)
            self._save_ugoira_gif(frame_blobs, saved_gif_path, delay_info)
            self._apply_download_mtime(saved_gif_path, my_time)
            if self._stats_collector is not None:
                self._stats_collector.report_file(True)
            self._enqueue_jxl(saved_gif_path)
            return 0
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError):
            # Network/proxy failures must propagate so the scheduler-aware caller
            # can disable the cookie/proxy for this run.
            raise
        except Exception as err:
            # Never print self.cookies — it is a live Pixiv session credential.
            print(err)
        return [url, my_time.strftime('%Y%m%d_%H%M%S')]

    _JPG_USER_AGENT = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/99.0.4844.82 Safari/537.36'
    )

    def _jpg_advance_timetag(self):
        """Reserve and return a unique timetag for this download (timelock-guarded)."""
        with self.timelock:
            timetag = self.download_time.strftime('%Y%m%d_%H%M%S')
            self.download_time += datetime.timedelta(seconds=1)
        return timetag

    @staticmethod
    def _jpg_extract_page_and_format(url):
        """Pull the page number and file extension out of a pximg URL."""
        url_str = str(url)
        page = url_str.rsplit('_', 1)[1].rsplit('.', 1)[0]
        picture_format = url_str.rsplit('.', 1)[1]
        return page, picture_format

    def _jpg_build_headers(self, pid, pid_cookie, need_cookie):
        """Headers for the jpg fetch — same shape as gif but with a different UA."""
        headers = self._build_artwork_headers(pid, pid_cookie, need_cookie)
        headers['User-Agent'] = self._JPG_USER_AGENT
        return headers

    def _jpg_resolve_filename(self, pid, page_suffix, ext, tag, timetag):
        """Build the final filename, falling back to a safe default on any error."""
        try:
            hashtag = self._build_hashtag_text(tag, max_len=230)
            return self._build_download_filename(
                pid,
                page_suffix=page_suffix,
                ext=ext,
                hashtag=hashtag,
                timetag=timetag,
                notag=self.notag,
                notime=self.notime,
                template=getattr(self, "filename_template", ""),
            )
        except Exception:
            return 'illust_' + pid + page_suffix + timetag + '.' + ext

    def _apply_download_mtime(self, filepath, when):
        """Set the saved file's atime/mtime to its timetag (download.set_file_mtime).

        ``when`` is either a 'YYYYMMDD_HHMMSS' timetag string or a datetime.
        Keeps the on-disk timestamp consistent with the timestamp embedded in
        the filename. Best-effort: never fails the download.
        """
        if not getattr(self, "set_file_mtime", False):
            return
        try:
            if isinstance(when, str):
                when = datetime.datetime.strptime(when, '%Y%m%d_%H%M%S')
            ts = when.timestamp()
            os.utime(filepath, (ts, ts))
        except Exception:
            pass

    def _jpg_stream_to_disk(self, htmlfile, filepath):
        """Stream the HTTP body to disk in 1 KiB chunks; returns total bytes written."""
        size = 0
        chunk_size = 1024
        with open(filepath, 'wb') as file:
            for data in htmlfile.iter_content(chunk_size=chunk_size):
                file.write(data)
                size += len(data)
        return size

    def _jpg_attempt(self, url, session, timetag):
        """One download attempt. Returns 0 on success, None on a recoverable error."""
        pid, pid_cookie, need_cookie = self._resolve_pid_and_cookie(url, source="step4")
        normalized = self._load_artwork_metadata(pid, pid_cookie)
        if not normalized:
            raise ValueError("Pixiv_info 回傳格式異常")
        tag, like, pagecount, img_url = normalized
        if like == 404 and tag == 404:
            db = getattr(self, "_metadata_db", None)
            if db is not None:
                with contextlib.suppress(Exception):
                    db.mark_artwork_revoked(pid)
            return 0  # treat as success — nothing to download
        page, picture_format = self._jpg_extract_page_and_format(url)
        headers = self._jpg_build_headers(pid, pid_cookie, need_cookie)
        with contextlib.suppress(Exception):
            self._pid_cookie_used[str(pid)] = bool(need_cookie is True and pid_cookie)
        http = session if session is not None else requests
        htmlfile = http.get(url, headers=headers, stream=True, timeout=5)
        htmlfile.raise_for_status()
        if self._stats_collector is not None:
            label = _cookie_usage_label(pid_cookie, self.cookie_pool, self._cookie_alias_map)
            self._stats_collector.report_request(label)
        if htmlfile.status_code != 200:
            return 0
        name = self._jpg_resolve_filename(pid, page, picture_format, tag, timetag)
        target_dir = self._resolve_download_target_dir(str(tag), pid)
        filepath = os.path.join(target_dir, name)
        size = self._jpg_stream_to_disk(htmlfile, filepath)
        self._apply_download_mtime(filepath, timetag)
        if self._stats_collector is not None:
            self._stats_collector.report_bytes(size)
            self._stats_collector.report_file(True)
        self._enqueue_jxl(filepath)
        return 0

    def jpg_download(self, url, session=None):
        timetag = self._jpg_advance_timetag()
        last_err = None
        for i in range(0, 5):  # 最多重試 5 次，失敗就回傳錯誤
            try:
                return self._jpg_attempt(url, session, timetag)
            except (requests.exceptions.ProxyError,
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError):
                # Bypass the retry loop entirely — the proxy is dead, retrying
                # against the same proxy will not help. Let the scheduler disable
                # this cookie via release(ok=False).
                raise
            except Exception as err:
                last_err = err
                if i < 4:
                    time.sleep(min(30.0, (2 ** i) + pyrandom.random()))
                    continue
        print(last_err)
        if self._stats_collector is not None:
            self._stats_collector.report_file(False)
        return [url, timetag]
