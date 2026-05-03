import threading
import queue as _queue
import time

import requests

from app.core.worker_event import WorkerEvent
from pixiv_api import *
from app.core.pixiv_thread_utils import (
    cookie_usage_label,
    format_cookie_usage_summary,
    normalize_cookie_entries,
    normalize_cookie_pool,
)
# Backward-compatible aliases — implementations live in pixiv_thread_utils
_normalize_cookie_entries = normalize_cookie_entries
_normalize_cookie_pool = normalize_cookie_pool
_cookie_usage_label = cookie_usage_label
_format_cookie_usage_summary = format_cookie_usage_summary


NETWORK_RETRY_ATTEMPTS = 5
NETWORK_RETRY_WAIT_SEC = 60
_NETWORK_RETRY_EXCEPTIONS = (
    requests.exceptions.ProxyError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
)


def _coerce_to_rule_iterable(raw_rules):
    """Wrap a single dict in a list; return [] for non-iterable inputs."""
    if isinstance(raw_rules, dict):
        return [raw_rules]
    if isinstance(raw_rules, (list, tuple, set)):
        return raw_rules
    return []


def _normalize_rule_tags(raw_tags):
    """Coerce a raw tags field to a deduplicated list of lowercase strings."""
    if isinstance(raw_tags, str):
        items = [raw_tags]
    elif isinstance(raw_tags, (list, tuple, set)):
        items = list(raw_tags)
    else:
        items = [raw_tags]
    out = []
    for tag in items:
        text = str(tag or "").strip().lower()
        if text:
            out.append(text)
    return list(dict.fromkeys(out))


def _parse_rule_min_like(rule):
    """Coerce min_like / like_num to a non-negative int, defaulting to 0 on error."""
    try:
        raw = str(rule.get("min_like", rule.get("like_num", 0)) or 0).strip() or 0
        return int(float(raw))
    except Exception:
        return 0


def _normalize_one_rule(rule, index):
    """Validate + normalize one rule dict. Returns the entry dict or None to skip."""
    if not isinstance(rule, dict):
        return None
    tags = _normalize_rule_tags(rule.get("tags", rule.get("tag", [])))
    min_like = _parse_rule_min_like(rule)
    if min_like <= 0 or not tags:
        return None
    label = str(rule.get("label", rule.get("name", f"rule_{index + 1}"))).strip()
    return {"label": label, "tags": tags, "min_like": min_like}


def _normalize_special_like_rules(raw_rules):
    rules = _coerce_to_rule_iterable(raw_rules)
    normalized = []
    for index, rule in enumerate(rules):
        entry = _normalize_one_rule(rule, index)
        if entry is not None:
            normalized.append(entry)
    return normalized


def _read_rule_tags_and_min(rule, to_int):
    """Pull (tags, min_like) out of a normalized rule dict, returning (None, 0) on error."""
    try:
        return rule.get("tags", []), to_int(rule.get("min_like", 0), 0) or 0
    except Exception:
        return None, 0


def _rule_matches_artwork(rule_tags, artwork_tags, tag_hit):
    return any(tag_hit(target, artwork_tags) for target in rule_tags or [])


def _resolve_like_threshold(base_like, artwork_tags, special_like_rules, tag_hit, to_int):
    threshold = to_int(base_like, 0) or 0
    matched_rules = []
    for rule in special_like_rules or []:
        rule_tags, rule_min_like = _read_rule_tags_and_min(rule, to_int)
        if rule_min_like <= 0 or rule_tags is None:
            continue
        if _rule_matches_artwork(rule_tags, artwork_tags, tag_hit):
            matched_rules.append(rule)
            if rule_min_like > threshold:
                threshold = rule_min_like
    return threshold, matched_rules


def _is_ai_artwork_tagged(artwork_tags, tag_hit):
    ai_markers = (
        "ai生成",
        "aiイラスト",
        "ai-generated",
        "ai generated",
        "ai art",
        "aiart",
        "aigenerated",
        "生成ai",
    )
    for marker in ai_markers:
        if tag_hit(marker, artwork_tags):
            return True
    return False

class PauseableThread(threading.Thread):
    """Base class: pause/resume/stop with countdown support via queue.Queue."""

    def __init__(self, q: _queue.Queue, scheduler=None):
        super().__init__(daemon=True)
        self._q = q
        self._pause_event = threading.Event()
        self._pause_event.set()   # not paused by default
        self._stop_event = threading.Event()
        self._scheduler = scheduler  # AccountScheduler | None

    def pause(self):
        self._pause_event.clear()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已暫停</font></p>"))
        # Hooks may do disk I/O (e.g. flushing partial-progress JSON). Run
        # them off the caller's thread so a UI click handler isn't blocked.
        threading.Thread(target=self._on_pause_hook, daemon=True).start()

    def resume(self):
        self._pause_event.set()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已繼續</font></p>"))

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()   # unblock any waiting pause
        self._q.put(WorkerEvent("output", "<p><font color='red'>已停止</font></p>"))
        threading.Thread(target=self._on_stop_hook, daemon=True).start()

    def _on_pause_hook(self):
        pass

    def _on_stop_hook(self):
        pass

    def _sleep_with_countdown(self, delay):
        """Sleep with pause/stop support; emits countdown ticks."""
        if delay <= 0:
            return
        for remaining in range(int(delay), 0, -1):
            if self._stop_event.is_set():
                break
            # Poll pause every 0.5s so stop() wakes us promptly even mid-pause.
            while not self._pause_event.is_set():
                if self._stop_event.is_set():
                    break
                self._pause_event.wait(timeout=0.5)
            if self._stop_event.is_set():
                break
            try:
                self._q.put(WorkerEvent("countdown", remaining))
            except Exception:
                pass
            # _stop_event.wait returns True if set; lets us break out instantly.
            if self._stop_event.wait(timeout=1.0):
                break
        try:
            self._q.put(WorkerEvent("countdown", 0))
        except Exception:
            pass

    def _acquire_account(self):
        """Acquire next available account from scheduler, or None if no scheduler."""
        if self._scheduler is None:
            return None
        return self._scheduler.acquire()

    def _release_account(self, account, ok: bool = True) -> None:
        """Release account back to scheduler. Safe no-op if scheduler/account is None."""
        if self._scheduler is None or account is None:
            return
        self._scheduler.release(account, ok=ok)

    def _emit_output(self, html: str) -> None:
        try:
            self._q.put(WorkerEvent("output", html))
        except Exception:
            pass

    def _wait_interruptible(self, seconds: float) -> bool:
        """Sleep for ``seconds``, polling stop/pause every 0.5 s.

        Returns True if the full duration elapsed, False if stop fired.
        Paused time does not count toward the budget.
        """
        if seconds <= 0:
            return not self._stop_event.is_set()
        elapsed = 0.0
        while elapsed < seconds:
            if self._stop_event.is_set():
                return False
            if not self._pause_event.is_set():
                self._pause_event.wait(timeout=0.5)
                continue
            slice_s = min(0.5, seconds - elapsed)
            if self._stop_event.wait(timeout=slice_s):
                return False
            elapsed += slice_s
        return True

    def _run_with_network_retry(self, work_label: str, fn):
        """Run ``fn()`` with up to NETWORK_RETRY_ATTEMPTS attempts on the
        scheduler network triple. Returns ``(ok, result, last_exc)``.

        Success returns ``(True, result, None)``. Exhaustion or stop during
        wait returns ``(False, None, last_exc)``. Non-network exceptions
        propagate unchanged on first occurrence.
        """
        if self._stop_event.is_set():
            return False, None, None
        last_exc = None
        for attempt in range(1, NETWORK_RETRY_ATTEMPTS + 1):
            try:
                return True, fn(), None
            except _NETWORK_RETRY_EXCEPTIONS as err:
                last_exc = err
                if attempt < NETWORK_RETRY_ATTEMPTS:
                    self._emit_output(
                        f"<p><font color='#b58900'>{work_label} 第 {attempt}/"
                        f"{NETWORK_RETRY_ATTEMPTS} 次失敗"
                        f"（{err.__class__.__name__}），"
                        f"{NETWORK_RETRY_WAIT_SEC} 秒後重試</font></p>"
                    )
                    if not self._wait_interruptible(NETWORK_RETRY_WAIT_SEC):
                        return False, None, last_exc
                else:
                    self._emit_output(
                        f"<p><font color='red'>{work_label} 重試 "
                        f"{NETWORK_RETRY_ATTEMPTS} 次仍失敗"
                        f"（{err.__class__.__name__}），停用此 Cookie</font></p>"
                    )
        return False, None, last_exc

