"""Application-wide logging setup.

Writes to %APPDATA%/pixiv_download/app.log (rotating) plus stderr.
Installs sys.excepthook + threading.excepthook so unhandled exceptions
on any thread land in the log instead of disappearing silently.

Call init_logging() exactly once at program start (idempotent).
"""
from __future__ import annotations
import logging
import logging.handlers
import os
import sys
import threading
from typing import Optional

_INITIALIZED = False
_LOG_PATH: Optional[str] = None


def _default_log_dir() -> str:
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    path = os.path.join(appdata, "pixiv_download")
    os.makedirs(path, exist_ok=True)
    return path


def _install_excepthooks(logger: logging.Logger) -> None:
    prev_sys = sys.excepthook

    def _sys_hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            prev_sys(exc_type, exc, tb)
            return
        logger.error("Unhandled exception", exc_info=(exc_type, exc, tb))
        prev_sys(exc_type, exc, tb)

    sys.excepthook = _sys_hook

    if hasattr(threading, "excepthook"):
        prev_thread = threading.excepthook

        def _thread_hook(args) -> None:
            logger.error(
                "Unhandled exception in thread %s",
                getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            prev_thread(args)

        threading.excepthook = _thread_hook


def init_logging() -> str:
    """Initialise application logging. Returns the log file path."""
    global _INITIALIZED, _LOG_PATH
    if _INITIALIZED:
        return _LOG_PATH or ""

    log_dir = _default_log_dir()
    log_path = os.path.join(log_dir, "app.log")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_h = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_h.setLevel(logging.INFO)
    file_h.setFormatter(fmt)

    stream_h = logging.StreamHandler(stream=sys.stderr)
    stream_h.setLevel(logging.WARNING)
    stream_h.setFormatter(fmt)

    # Replace existing handlers so re-runs in the same interpreter don't
    # double-log (matters during testing).
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(file_h)
    root.addHandler(stream_h)

    _install_excepthooks(logging.getLogger("pixiv"))

    _INITIALIZED = True
    _LOG_PATH = log_path
    logging.getLogger("pixiv.boot").info("logging initialised: %s", log_path)
    return log_path


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger. init_logging() should run first."""
    if not _INITIALIZED:
        init_logging()
    return logging.getLogger(name)
