import contextlib
import random as pyrandom
import threading
import time
import queue as _queue

import requests

from app.core.worker_event import WorkerEvent
from app import i18n
from pixiv_api import *
from app.core.pixiv_thread_utils import (
    cookie_usage_label,
    format_cookie_usage_summary,
    normalize_cookie_entries,
    normalize_cookie_pool,
    normalize_pid,
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

# Per-page download bounds. The requests ``timeout`` tuple is a PER-RECV
# socket deadline only — urllib3 never sets ``Timeout.total`` from requests,
# so a trickling/half-open connection (a few bytes inside every read-timeout
# window) keeps ``iter_content`` looping forever without raising. The CONNECT
# and READ values below bound a fully-silent socket; the wall-clock
# DOWNLOAD_DEADLINE_SEC (enforced in Python between chunks by
# ``_stream_to_sink``) is what bounds the trickle and honours Stop. Both
# layers are required. See the 2026-06-21 download-hang investigation.
DOWNLOAD_CONNECT_TIMEOUT = 10
DOWNLOAD_READ_TIMEOUT = 30
DOWNLOAD_DEADLINE_SEC = 120.0


class DownloadStopped(Exception):
    """Raised by ``_stream_to_sink`` when stop_event fires mid-transfer.

    A user Stop is NOT a failure: callers must leave the page pending
    (no err_url, no attempt_count bump, no cookie disable) — mirror the
    pre-fetch stop path in ``gif_or_jpg``.
    """


class DownloadDeadlineExceeded(Exception):
    """Raised by ``_stream_to_sink`` when the total wall-clock budget is hit.

    A deadline IS a page failure (the connection wedged/trickled). Callers
    settle it as the normal fail-list ``[url, timetag]`` so the PID stays
    pending and is retried next run — but the cookie is NOT disabled (a
    deadline is not the cookie's fault), so this must never be raised across
    a scheduler-aware boundary as a network exception.
    """


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
        self._cookie_usage_lock = threading.Lock()

    def pause(self):
        self._pause_event.clear()
        self._q.put(WorkerEvent("output", f"<p><font color='red'>{i18n.t('log.paused')}</font></p>"))
        # Hooks may do disk I/O (e.g. flushing partial-progress JSON). Run
        # them off the caller's thread so a UI click handler isn't blocked.
        threading.Thread(target=self._on_pause_hook, daemon=True).start()

    def resume(self):
        self._pause_event.set()
        self._q.put(WorkerEvent("output", f"<p><font color='red'>{i18n.t('log.resumed')}</font></p>"))

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()   # unblock any waiting pause
        self._q.put(WorkerEvent("output", f"<p><font color='red'>{i18n.t('log.stopped')}</font></p>"))
        threading.Thread(target=self._on_stop_hook, daemon=True).start()

    def _on_pause_hook(self):
        pass

    def _on_stop_hook(self):
        pass

    def __del__(self):
        # Best-effort shutdown of a subclass-attached ThreadPoolExecutor.
        # ``wait=False`` lets us return immediately; pending tasks finish
        # on their own.  Swallow everything — __del__ on a dying interpreter
        # cannot raise.
        try:
            executor = getattr(self, "executor", None)
            if executor is not None:
                executor.shutdown(wait=False)
        except Exception:
            pass

    def _record_cookie_usage(self, stage, pid, cookie_value):
        """Increment the per-stage cookie-usage counter for ``cookie_value``
        and return its display label.  Counts each PID once per stage; relies
        on subclass-initialized ``_cookie_usage_counts`` / ``_cookie_usage_seen``
        dicts (keyed by stage string)."""
        stage_key = str(stage or "").strip().lower()
        if stage_key not in self._cookie_usage_counts:
            return ""
        cookie_text = str(cookie_value or "").strip()
        label = cookie_usage_label(cookie_text, self.cookie_pool, self._cookie_alias_map)
        if not cookie_text:
            return label
        pid_key = normalize_pid(pid) or str(pid)
        try:
            with self._cookie_usage_lock:
                seen = self._cookie_usage_seen.setdefault(stage_key, set())
                if pid_key in seen:
                    return label
                seen.add(pid_key)
                counts = self._cookie_usage_counts.setdefault(stage_key, {})
                counts[label] = int(counts.get(label, 0)) + 1
        except Exception:
            pass
        return label

    def _emit_cookie_usage_summary(self, stage, title):
        """Push a one-line summary (e.g. ``[Step3 Cookie統計] foo×12 bar×3``)
        of the per-stage usage counts to the worker queue."""
        try:
            stage_key = str(stage or "").strip().lower()
            counts = self._cookie_usage_counts.get(stage_key, {}) if isinstance(self._cookie_usage_counts, dict) else {}
            summary = format_cookie_usage_summary(counts, self.cookie_pool, self._cookie_alias_map)
            self._q.put(WorkerEvent("output", f"<p><font color='gray'>[{title}] {summary}</font></p>"))
        except Exception:
            pass

    def _select_cookie_for_pid(self, pid):
        """Pick a cookie for ``pid`` and cache it so the same PID always
        retries with the same cookie.  Falls back to ``self.cookies`` when
        the pool is empty.  Requires subclass-initialized
        ``_pid_cookie_selection`` dict; the alias-map cache
        ``_pid_cookie_alias_selection`` is updated when present."""
        pid_key = normalize_pid(pid) or str(pid)
        try:
            if pid_key in self._pid_cookie_selection:
                return self._pid_cookie_selection.get(pid_key, "")
        except Exception:
            pass
        if self.cookie_pool:
            selected = pyrandom.choice(self.cookie_pool)
        else:
            selected = str(self.cookies or "").strip()
        try:
            # setdefault under the cookie-usage lock so two pool workers racing
            # on the same fresh PID agree on one cookie (first writer wins).
            with self._cookie_usage_lock:
                selected = self._pid_cookie_selection.setdefault(pid_key, selected)
                alias_cache = getattr(self, "_pid_cookie_alias_selection", None)
                if alias_cache is not None:
                    alias_cache.setdefault(
                        pid_key,
                        cookie_usage_label(selected, self.cookie_pool, self._cookie_alias_map),
                    )
        except Exception:
            pass
        return selected

    def _get_meta(self, pid_key):
        """Return metadata for ``pid_key``: in-memory ``self.url_meta`` first,
        then SQLite ``self._metadata_db`` lookup.  Returns ``{}`` when nothing
        is known about the PID."""
        pid_key = str(pid_key)
        lock = getattr(self, "_url_meta_lock", None)
        url_meta = getattr(self, "url_meta", None)
        if isinstance(url_meta, dict):
            if lock is not None:
                with lock:
                    cached = url_meta.get(pid_key)
            else:
                cached = url_meta.get(pid_key)
        else:
            cached = None
        if isinstance(cached, dict) and cached:
            return cached
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            try:
                meta = db.get_meta(pid_key)
                if isinstance(meta, dict) and meta:
                    return meta
            except Exception:
                pass
        return {}

    def _load_initial_url_meta(self):
        """Default: empty dict.  Steps 3/4 use DB-on-demand reads via
        ``_get_meta``; the in-memory dict is a write-through cache only."""
        return {}

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

    def _release_account_after_work(
        self, account, ok: bool = True, neutral: bool = False
    ) -> None:
        """Release an account per the work-unit contract (the proven Step-4
        pattern, factored out so Steps 2/3/combined cannot drift from it).

        Call from a ``finally`` with ``neutral=True`` set in an ``except`` so a
        NON-network exception (disk/decode/sqlite error — not the cookie's
        fault) releases neutrally. A user Stop during the work also releases
        neutrally. In both cases the cookie is neither disabled (``ok=False`` ->
        on_disable persists ``失効`` to settings) nor credited with a success
        (``ok=True`` refreshes the trust window). Only genuine network-retry
        exhaustion (``ok=False`` off the stop path) disables the cookie."""
        if account is None:
            return
        if neutral or (not ok and self._stop_event.is_set()):
            if self._scheduler is not None:
                self._scheduler.release_neutral(account)
        else:
            # Defer to _release_account (which guards a None scheduler itself);
            # this keeps the single release seam that Steps 2/3/4 stub in tests.
            self._release_account(account, ok=ok)

    def _r18_aware_like_base(self, artwork_tags):
        """Effective minimum-like base: raised to r18_like_num for R-18 works
        when stricter. artwork_tags are already normalized (lowercased). Default
        r18_like_num=0 -> always returns like_num (zero behavior change)."""
        base = int(getattr(self, "like_num", 0) or 0)
        r18 = int(getattr(self, "r18_like_num", 0) or 0)
        if r18 <= base:
            return base
        tags = artwork_tags or []
        is_r18g = any("r-18g" in str(t) for t in tags)
        is_r18 = (not is_r18g) and any(str(t) == "r-18" for t in tags)
        return r18 if is_r18 else base

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

    def _stream_to_sink(self, response, write, *, chunk_size=65536, deadline_sec=None):
        """Drain a streamed ``requests`` Response into ``write(bytes)`` with a
        TOTAL wall-clock deadline plus stop/pause awareness.

        The per-recv read timeout on the originating request bounds a fully
        silent socket; this loop bounds a *trickle* (the confirmed wedge mode)
        and lets Stop interrupt an in-flight transfer. Paused time does not
        count toward the deadline (mirrors ``_wait_interruptible``). The
        response is ALWAYS closed (success, deadline, stop, error) so a
        mid-stream abort never leaks the pooled socket.

        Raises ``DownloadStopped`` on stop and ``DownloadDeadlineExceeded`` on
        the wall-clock deadline; both propagate to the caller, which decides how
        to settle the page (stop -> pending; deadline -> failed). ``write`` is a
        sink callback, e.g. ``file.write`` or ``bytearray.extend``.
        """
        if deadline_sec is None:
            deadline_sec = getattr(self, "_download_deadline_sec", DOWNLOAD_DEADLINE_SEC)
        deadline = time.monotonic() + float(deadline_sec)
        # Control events always exist on a real PauseableThread; tolerate their
        # absence (e.g. a unit test instantiating via __new__) by degrading to a
        # plain deadline-only stream.
        pause_event = getattr(self, "_pause_event", None)
        stop_event = getattr(self, "_stop_event", None)
        try:
            for data in response.iter_content(chunk_size=chunk_size):
                # Refund paused time so a long human pause never trips the
                # deadline. _pause_event is SET while running, CLEAR while paused.
                while pause_event is not None and not pause_event.is_set():
                    if stop_event is not None and stop_event.is_set():
                        raise DownloadStopped()
                    waited_from = time.monotonic()
                    pause_event.wait(timeout=0.5)
                    deadline += time.monotonic() - waited_from
                if stop_event is not None and stop_event.is_set():
                    raise DownloadStopped()
                if time.monotonic() >= deadline:
                    raise DownloadDeadlineExceeded(
                        f"download exceeded {float(deadline_sec):.0f}s total deadline"
                    )
                if data:
                    write(data)
        finally:
            with contextlib.suppress(Exception):
                response.close()

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
                        "<p><font color='#b58900'>" + i18n.t(
                            "log.retry.attempt", work=work_label, attempt=attempt,
                            total=NETWORK_RETRY_ATTEMPTS, err=err.__class__.__name__,
                        ) + "</font></p>"
                    )
                    if not self._wait_interruptible(NETWORK_RETRY_WAIT_SEC):
                        return False, None, last_exc
                else:
                    self._emit_output(
                        "<p><font color='red'>" + i18n.t(
                            "log.retry.exhausted", work=work_label,
                            total=NETWORK_RETRY_ATTEMPTS, err=err.__class__.__name__,
                        ) + "</font></p>"
                    )
        return False, None, last_exc

