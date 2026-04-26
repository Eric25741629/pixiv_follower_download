"""Backward-compatible shim. Source of truth: app/core/pixiv_api.py.

Kept so legacy `from pixiv_api import *` / `import pixiv_api` call sites resolve
to the canonical implementation when running from the repo root.
"""
from app.core.pixiv_api import *  # noqa: F401,F403
from app.core import pixiv_api as _impl

__all__ = [name for name in dir(_impl) if not name.startswith("_")]
