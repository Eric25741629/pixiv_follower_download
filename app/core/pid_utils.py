"""PID normalization + filename mining helpers.

Extracted verbatim from ``pixiv_thread_utils`` (file-size refactor). These are
pure, stdlib-only helpers with zero coupling to the rest of the module. They are
performance-sensitive — ``normalize_pid`` runs over the ~1.1M-element closed set
on every Run All — so the digit fast-path below is load-bearing.

``pixiv_thread_utils`` re-exports every name here, so existing
``from app.core.pixiv_thread_utils import normalize_pid`` callers keep working.
"""
import os
import re


def normalize_pid(value):
    s = str(value).strip()
    if not s:
        return ""
    # Fast path: a bare digit string (the overwhelmingly common case — every
    # PID in the DB closed set is already normalized) needs no transforms.
    # ``'_' split``, ``replace('p0')`` and ``re.search`` are all no-ops on
    # pure digits, so this is exactly equivalent and ~5x cheaper. Matters
    # because normalize_pid_set runs this over the ~1.1M-element closed set.
    if s.isdigit():
        return s
    if '_' in s:
        s = s.split('_', 1)[0]
    s = s.replace('p0', '')
    m = re.search(r"\d+", s)
    if m:
        return m.group(0)
    return s


def normalize_pid_set(values):
    out = set()
    if not values:
        return out
    try:
        for v in values:
            pid = normalize_pid(v)
            if pid:
                out.add(pid)
    except Exception:
        pid = normalize_pid(values)
        if pid:
            out.add(pid)
    return out


def canonicalize_pximg_url_for_storage(url):
    """
    Normalize pximg original URL to a stable storage form without hash segment.
    Example:
      .../139112835-c476..._p0.jpg -> .../139112835_p0.jpg
    """
    try:
        s = str(url).strip()
        if not s:
            return s
        head, tail = s.rsplit("/", 1)
        # Keep only the PID and page marker in filename for all_url.txt readability.
        tail2 = re.sub(
            r"^(\d{5,12})-[a-f0-9]+(_(?:p\d+|ugoira\d+)\.[A-Za-z0-9]+)$",
            r"\1\2",
            tail,
            flags=re.IGNORECASE,
        )
        return head + "/" + tail2
    except Exception:
        return str(url)


_PID_FROM_NAME_PATTERNS = [
    re.compile(r"PID[=\s_-]?(\d{5,12})", re.IGNORECASE),
    re.compile(r"illust[_-]?(\d{5,12})", re.IGNORECASE),
    re.compile(r"(\d{5,12})_p\d+", re.IGNORECASE),
    re.compile(r"(\d{5,12})p\d+", re.IGNORECASE),
    # pximg original filename: 139112835-<hash>_p0.jpg
    re.compile(r"^(\d{5,12})-[a-f0-9]+_p\d+", re.IGNORECASE),
]


def _extract_pid_candidates_from_name(file_name):
    out = set()
    try:
        name = os.path.basename(str(file_name))
    except Exception:
        return out
    for pattern in _PID_FROM_NAME_PATTERNS:
        try:
            for pid in pattern.findall(name):
                n = normalize_pid(pid)
                if n:
                    out.add(n)
        except Exception:
            pass
    try:
        stem, _ = os.path.splitext(name)
        if re.fullmatch(r"\d{5,12}", stem):
            out.add(stem)
    except Exception:
        pass
    return out
