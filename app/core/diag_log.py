"""Dedicated diagnostic file logs for cross-referencing runtime behavior.

Three rotating files under ``%APPDATA%/pixiv_download/logs/``, kept separate
from ``app.log`` (``propagate = False``) so each trace can be diffed on its own
and against the others:

  ui_events.log : every ``WorkerEvent`` the dispatcher delivers to the UI
                  (phase / countdown / progress / page_progress / output / ...),
                  one line per event — instrumented in the "update component"
                  (``EventDispatcher``). This is the UI-communication trace.
  download.log  : per-PID / per-page download lifecycle (start, saved, done,
                  failed) with sizes.
  worker.log    : worker lifecycle — step / PID / account-acquire / query /
                  download spans with START + END + elapsed seconds, so an
                  absorbed (zero-second) cooldown shows up explicitly.

Every line carries a millisecond timestamp, so the three logs line up with each
other and with ``app.log`` for cross-referencing. Disabled globally via
``configure(enabled=False)`` (wired to ``diagnostics.verbose_logs``).
"""
from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import threading
import time

# Channel -> on-disk file stem. Public names callers pass to the helpers below.
UI = "ui_events"
DOWNLOAD = "download"
WORKER = "worker"

_LOG_SUBDIR = "logs"
_loggers: dict[str, logging.Logger] = {}
_loggers_lock = threading.Lock()
_enabled = True
# The UI per-event trace (ui_events.log) fires once per WorkerEvent — the
# hottest path in the app (every progress/output/countdown tick, with an HTML-
# strip regex per output) — and only mirrors events already visible elsewhere.
# It is therefore OPT-IN (default off): without it the dispatcher skips the
# per-event f-string + summary() + synchronous file write entirely. The
# worker/download channels stay on by default (per-PID/page, low rate, the
# debug-critical traces used for the download-hang investigation).
_ui_trace_enabled = False


def configure(enabled: bool) -> None:
    """Globally enable/disable diagnostic logging (call once at startup)."""
    global _enabled
    _enabled = bool(enabled)


def configure_ui_trace(enabled: bool) -> None:
    """Enable the high-frequency per-event UI trace (ui_events.log); off by
    default. Wire to diagnostics.verbose_logs at startup."""
    global _ui_trace_enabled
    _ui_trace_enabled = bool(enabled)


def ui_trace_enabled() -> bool:
    """Cheap guard for the dispatcher hot path: skip building the per-event
    f-string + summary() unless the UI trace is on."""
    return _enabled and _ui_trace_enabled


def _logs_dir() -> str:
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    path = os.path.join(appdata, "pixiv_download", _LOG_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def _get(channel: str) -> logging.Logger | None:
    if not _enabled:
        return None
    lg = _loggers.get(channel)
    if lg is not None:
        return lg
    # Lazy init under a lock (double-checked) so two threads racing on first
    # use of a channel can't each attach a RotatingFileHandler (double-logged
    # lines).
    with _loggers_lock:
        lg = _loggers.get(channel)
        if lg is not None:
            return lg
        lg = logging.getLogger(f"pixiv.diag.{channel}")
        lg.setLevel(logging.INFO)
        lg.propagate = False  # keep diagnostics out of app.log / stderr
        if not lg.handlers:  # idempotent across re-imports / re-inits
            try:
                handler = logging.handlers.RotatingFileHandler(
                    os.path.join(_logs_dir(), f"{channel}.log"),
                    maxBytes=8 * 1024 * 1024, backupCount=5, encoding="utf-8",
                )
                # Default asctime already carries ",mmm" millisecond precision.
                handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                lg.addHandler(handler)
            except Exception:
                return None
        _loggers[channel] = lg
        return lg


def log(channel: str, message: str) -> None:
    """Write one line to a diagnostic channel (best-effort, never raises)."""
    if channel == UI and not _ui_trace_enabled:
        return  # per-event UI trace is opt-in (see configure_ui_trace)
    lg = _get(channel)
    if lg is None:
        return
    with contextlib.suppress(Exception):
        lg.info(message)


def summary(data, *, limit: int = 160) -> str:
    """Compact one-line repr of a WorkerEvent payload for the UI trace.

    Strips HTML tags from ``output`` strings and truncates long values so the
    ui_events.log stays readable while still showing what was sent.
    """
    try:
        if data is None:
            return "None"
        if isinstance(data, str):
            text = _strip_html(data).strip()
            return text if len(text) <= limit else text[: limit - 1] + "…"
        text = repr(data)
        return text if len(text) <= limit else text[: limit - 1] + "…"
    except Exception:
        return "<unreprable>"


_TAG_RE = None


def _strip_html(text: str) -> str:
    global _TAG_RE
    if _TAG_RE is None:
        import re
        _TAG_RE = re.compile(r"<[^>]+>")
    return _TAG_RE.sub("", text)


@contextlib.contextmanager
def span(channel: str, label: str):
    """Log ``START <label>`` then ``END <label> (Xs)`` around a block.

    The elapsed seconds make an absorbed/zero-duration wait obvious in the
    trace (e.g. an account cooldown that the download time already covered).
    """
    log(channel, f"START {label}")
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        log(channel, f"END   {label} ({dt:.2f}s)")
