"""Backward-compatible shim. Source of truth: app/core/pixiv_thread.py."""
from app.core.pixiv_thread import *  # noqa: F401,F403
from app.core import pixiv_thread as _impl

__all__ = list(getattr(_impl, "__all__", [n for n in dir(_impl) if not n.startswith("_")]))
