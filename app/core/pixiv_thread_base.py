import threading
import queue as _queue
import time
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


def _normalize_special_like_rules(raw_rules):
    normalized = []
    if isinstance(raw_rules, dict):
        raw_rules = [raw_rules]
    if not isinstance(raw_rules, (list, tuple, set)):
        return normalized
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            continue
        raw_tags = rule.get("tags", rule.get("tag", []))
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        elif not isinstance(raw_tags, (list, tuple, set)):
            raw_tags = [raw_tags]
        tags = []
        for tag in raw_tags:
            text = str(tag or "").strip().lower()
            if text:
                tags.append(text)
        tags = list(dict.fromkeys(tags))
        try:
            min_like = int(float(str(rule.get("min_like", rule.get("like_num", 0)) or 0).strip() or 0))
        except Exception:
            min_like = 0
        if min_like <= 0 or not tags:
            continue
        label = str(rule.get("label", rule.get("name", f"rule_{index + 1}"))).strip()
        normalized.append({"label": label, "tags": tags, "min_like": min_like})
    return normalized


def _resolve_like_threshold(base_like, artwork_tags, special_like_rules, tag_hit, to_int):
    threshold = to_int(base_like, 0) or 0
    matched_rules = []
    for rule in special_like_rules or []:
        try:
            rule_tags = rule.get("tags", [])
            rule_min_like = to_int(rule.get("min_like", 0), 0) or 0
        except Exception:
            continue
        if rule_min_like <= 0:
            continue
        hit = False
        for target_tag in rule_tags:
            if tag_hit(target_tag, artwork_tags):
                hit = True
                break
        if hit:
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

    def __init__(self, q: _queue.Queue):
        super().__init__(daemon=True)
        self._q = q
        self._pause_event = threading.Event()
        self._pause_event.set()   # not paused by default
        self._stop_event = threading.Event()

    def pause(self):
        self._pause_event.clear()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已暫停</font></p>"))
        self._on_pause_hook()

    def resume(self):
        self._pause_event.set()
        self._q.put(WorkerEvent("output", "<p><font color='red'>已繼續</font></p>"))

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()   # unblock any waiting pause
        self._q.put(WorkerEvent("output", "<p><font color='red'>已停止</font></p>"))
        self._on_stop_hook()

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
            self._pause_event.wait()
            try:
                self._q.put(WorkerEvent("countdown", remaining))
            except Exception:
                pass
            time.sleep(1)
        try:
            self._q.put(WorkerEvent("countdown", 0))
        except Exception:
            pass


