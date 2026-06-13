"""JSON read + history-backup recovery helpers (file-size refactor).

Split out of ``pixiv_thread_utils.py``. ``safe_read_json`` is the plain
best-effort reader; ``read_json_with_recovery`` additionally restores a corrupt
file from the newest valid ``history/`` backup next to it. ``pixiv_thread_utils``
re-imports everything here so existing ``from app.core.pixiv_thread_utils import
safe_read_json`` / ``read_json_with_recovery`` callers are unchanged.

This module imports only the stdlib, so it has no import cycle with
``pixiv_thread_utils`` (the folder-scan module imports ``safe_read_json`` from
*here*, not from ``pixiv_thread_utils``).
"""
from __future__ import annotations

import json
import os


def safe_read_json(file_path, default=None):
    """Read a JSON file, returning `default` on any error (missing file, decode error, etc.)."""
    try:
        if not os.path.isfile(file_path):
            return default
        with open(file_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _safe_emit(emit, html):
    """Best-effort callback into the optional ``emit`` hook."""
    if emit is None:
        return
    try:
        emit(html)
    except Exception:
        pass


def _list_history_backups(hist_dir, base):
    """Return sibling backup files for ``base``, newest first."""
    try:
        candidates = [
            os.path.join(hist_dir, n) for n in os.listdir(hist_dir)
            if n.startswith(base + '.')
        ]
    except Exception:
        return []
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates


def _atomic_restore_file(file_path, value):
    """Replace a corrupt JSON file with ``value`` via tmp + os.replace."""
    try:
        tmp = file_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.replace(tmp, file_path)
    except Exception:
        pass


_NO_RECOVERY = object()


def _try_recover_from_history(file_path, emit):
    """Attempt to restore a corrupt JSON file from its history/ backups.

    Returns the recovered value on success, or the sentinel
    ``_NO_RECOVERY`` when no usable backup was found (so callers can
    distinguish a legitimately recovered ``None`` from "nothing found").
    """
    hist_dir = os.path.join(os.path.dirname(file_path), 'history')
    base = os.path.basename(file_path)
    if not os.path.isdir(hist_dir):
        _safe_emit(emit,
            f"<p><font color='red'>[警告] 無 history/ 備份可還原，"
            f"{base} 將以空值繼續。</font></p>"
        )
        return _NO_RECOVERY
    for cand in _list_history_backups(hist_dir, base):
        try:
            with open(cand, encoding='utf-8') as f:
                value = json.load(f)
        except Exception:
            continue
        _atomic_restore_file(file_path, value)
        n = len(value) if isinstance(value, (dict, list)) else 0
        _safe_emit(emit,
            f"<p><font color='green'>[還原] 已從 "
            f"history/{os.path.basename(cand)} 還原 {n} 筆</font></p>"
        )
        return value
    _safe_emit(emit,
        f"<p><font color='red'>[警告] history/ 內所有 {base} 備份"
        f"都無法解析，將以空值繼續。</font></p>"
    )
    return _NO_RECOVERY


def read_json_with_recovery(file_path, default=None, emit=None):
    """Read a JSON file; on parse failure, try to auto-recover from
    the latest valid backup in ``history/`` next to it.

    Returns ``(value, status)`` where status is one of:
      'missing'   — file doesn't exist; returned default
      'ok'        — file parsed cleanly
      'recovered' — file was corrupt; restored from history/<name>.<...>
      'corrupt'   — file was corrupt and no usable backup found

    ``emit`` is an optional callback ``emit(html_message)`` for surfacing
    recovery actions to the user (e.g., the worker thread's _q.put).
    """
    if not os.path.isfile(file_path):
        return default, 'missing'
    try:
        with open(file_path, encoding='utf-8') as f:
            return json.load(f), 'ok'
    except Exception as parse_err:
        _safe_emit(emit,
            f"<p><font color='red'>[警告] {os.path.basename(file_path)} "
            f"解析失敗（{type(parse_err).__name__}），嘗試從 history/ 還原...</font></p>"
        )
        recovered = _try_recover_from_history(file_path, emit)
        if recovered is _NO_RECOVERY:
            return default, 'corrupt'
        return recovered, 'recovered'
