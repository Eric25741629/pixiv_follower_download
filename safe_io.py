"""Backward-compatible shim. Source of truth: app/core/safe_io.py."""
from app.core.safe_io import *  # noqa: F401,F403
from app.core import safe_io as _impl

__all__ = [name for name in dir(_impl) if not name.startswith("_")]
