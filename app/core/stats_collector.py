from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Any


class StatsCollector:
    """Thread-safe statistics collector with session and lifetime counters."""

    def __init__(self, stats_path: str | None = None):
        self._lock = threading.Lock()
        self._stats_path = stats_path or os.path.join(
            os.getenv("APPDATA", ""), "pixiv_download", "stats.json"
        )
        self._session_bytes: int = 0
        self._session_files_ok: int = 0
        self._session_files_fail: int = 0
        self._session_requests: dict[str, int] = {}
        self._session_jxl_src: int = 0
        self._session_jxl_dst: int = 0
        self._session_started_at: float | None = None
        self._lifetime: dict[str, Any] = {
            "lifetime_bytes_downloaded": 0,
            "lifetime_files_downloaded": 0,
            "lifetime_sessions": 0,
            "last_run_time": None,
            "cookie_request_counts": {},
            "lifetime_jxl_src": 0,
            "lifetime_jxl_dst": 0,
        }
        self._load()

    def reset_session(self) -> None:
        with self._lock:
            self._session_bytes = 0
            self._session_files_ok = 0
            self._session_files_fail = 0
            self._session_requests = {}
            self._session_jxl_src = 0
            self._session_jxl_dst = 0
            self._session_started_at = time.monotonic()

    def report_bytes(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            self._session_bytes += n

    def report_file(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._session_files_ok += 1
            else:
                self._session_files_fail += 1

    def report_request(self, cookie_label: str) -> None:
        label = str(cookie_label or "").strip() or "Unknown"
        with self._lock:
            self._session_requests[label] = self._session_requests.get(label, 0) + 1

    def report_jxl(self, src_bytes: int, dst_bytes: int) -> None:
        with self._lock:
            self._session_jxl_src += max(0, src_bytes)
            self._session_jxl_dst += max(0, dst_bytes)

    def get_session_stats(self) -> dict[str, Any]:
        with self._lock:
            elapsed = 0.0
            if self._session_started_at is not None:
                elapsed = time.monotonic() - self._session_started_at
            return {
                "bytes": self._session_bytes,
                "files_ok": self._session_files_ok,
                "files_fail": self._session_files_fail,
                "requests": dict(self._session_requests),
                "jxl_src": self._session_jxl_src,
                "jxl_dst": self._session_jxl_dst,
                "elapsed": elapsed,
            }

    def get_lifetime_stats(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._lifetime)

    def save(self) -> None:
        try:
            with self._lock:
                self._lifetime["lifetime_bytes_downloaded"] += self._session_bytes
                self._lifetime["lifetime_files_downloaded"] += (
                    self._session_files_ok + self._session_files_fail
                )
                self._lifetime["lifetime_sessions"] += 1
                self._lifetime["last_run_time"] = datetime.now().isoformat()
                for label, count in self._session_requests.items():
                    cookie_counts = self._lifetime.setdefault("cookie_request_counts", {})
                    cookie_counts[label] = cookie_counts.get(label, 0) + count
                self._lifetime["lifetime_jxl_src"] += self._session_jxl_src
                self._lifetime["lifetime_jxl_dst"] += self._session_jxl_dst
                data = dict(self._lifetime)
            os.makedirs(os.path.dirname(self._stats_path), exist_ok=True)
            tmp = self._stats_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if os.path.exists(self._stats_path):
                os.replace(tmp, self._stats_path)
            else:
                os.rename(tmp, self._stats_path)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if not os.path.isfile(self._stats_path):
                return
            with open(self._stats_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._lifetime.update({
                    "lifetime_bytes_downloaded": int(data.get("lifetime_bytes_downloaded", 0)),
                    "lifetime_files_downloaded": int(data.get("lifetime_files_downloaded", 0)),
                    "lifetime_sessions": int(data.get("lifetime_sessions", 0)),
                    "last_run_time": data.get("last_run_time"),
                    "cookie_request_counts": data.get("cookie_request_counts") or {},
                    "lifetime_jxl_src": int(data.get("lifetime_jxl_src", 0)),
                    "lifetime_jxl_dst": int(data.get("lifetime_jxl_dst", 0)),
                })
        except Exception:
            pass

    @staticmethod
    def format_bytes(n: int) -> str:
        if n <= 0:
            return "0 B"
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.2f} KB"
        if n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.2f} MB"
        return f"{n / (1024 * 1024 * 1024):.2f} GB"

    @staticmethod
    def format_elapsed(seconds: float) -> str:
        if seconds <= 0:
            return "0:00:00"
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"
