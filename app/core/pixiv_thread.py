"""Backward-compatible re-export shim.

All original import paths still work:
    from app.core.pixiv_thread import download_thread
    from app.core import pixiv_thread; pixiv_thread.get_following(...)

Prefer importing directly from the split modules in new code.
"""
from app.core.pixiv_thread_base import (
    PauseableThread,
    _normalize_special_like_rules,
    _resolve_like_threshold,
    _is_ai_artwork_tagged,
    _normalize_cookie_entries,
    _normalize_cookie_pool,
    _cookie_usage_label,
    _format_cookie_usage_summary,
)
from app.core.thread_following import get_following
from app.core.thread_pid_scan import get_pixiv_author_imgID_Thread
from app.core.thread_url_fetch import get_img_url_thread
from app.core.thread_download import download_thread
from app.core.thread_test import test_thread

# Module-level globals (legacy; used only inside get_pixiv_author_imgID_Thread)
pid_num = 0
pid_len = 0

__all__ = [
    "PauseableThread",
    "_normalize_special_like_rules",
    "_resolve_like_threshold",
    "_is_ai_artwork_tagged",
    "_normalize_cookie_entries",
    "_normalize_cookie_pool",
    "_cookie_usage_label",
    "_format_cookie_usage_summary",
    "get_following",
    "get_pixiv_author_imgID_Thread",
    "get_img_url_thread",
    "download_thread",
    "test_thread",
    "pid_num",
    "pid_len",
]
