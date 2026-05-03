"""Filename sanitization + template rendering helpers.

Used by the download workers to produce filesystem-safe filenames and to
honor the user's optional ``download.filename_template`` setting.

The Windows filesystem (NTFS, FAT32, exFAT) reserves nine ASCII characters
in filenames: ``< > : " / \\ | ? *`` plus the ASCII control range U+0000–U+001F.
A short list of reserved DOS device names (CON, PRN, AUX, NUL, COM1..COM9,
LPT1..LPT9) also cannot be used as filename stems on Windows. Trailing
spaces and dots in a filename are silently stripped by Explorer and many
APIs, so we strip them upfront to avoid mismatches between expected and
actual on-disk names.
"""
from __future__ import annotations

import re

# Windows-reserved ASCII characters in filename components.
_FORBIDDEN_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# DOS device names — case-insensitive, applies to the *stem* (before ext).
_RESERVED_DEVICE_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})

# Default max length for a single filename component. 200 leaves room for
# the directory prefix under Windows' historical 260-char path limit.
DEFAULT_MAX_FILENAME_LEN = 200


def _split_stem_ext(s):
    """Split a sanitized name into (stem, ext); empty ext if there's no '.'"""
    stem, dot, ext = s.rpartition(".")
    if not dot:
        return s, ""
    return stem, ext


def _truncate_stem_to_budget(stem, ext, max_len, replacement):
    """Truncate the stem so stem + '.' + ext fits within max_len.

    Returns (stem, fallback_full) where fallback_full is non-None when the
    extension itself exceeds the budget — in that case the caller should
    return fallback_full directly.
    """
    budget = max_len - (len(ext) + (1 if ext else 0))
    if budget < 1:
        full = (stem + ("." + ext if ext else ""))[:max_len]
        return stem, (full or replacement)
    if len(stem) > budget:
        stem = stem[:budget].rstrip(". ").rstrip() or replacement
    return stem, None


def sanitize_filename(name: str, *, max_len: int = DEFAULT_MAX_FILENAME_LEN,
                      replacement: str = "_") -> str:
    """Return a filesystem-safe version of ``name``.

    - Replaces every Windows-forbidden character with ``replacement``.
    - Strips ASCII control characters.
    - Strips leading/trailing whitespace and dots (Explorer/Win32 do this
      silently, which causes "file vanished" surprises if we don't).
    - Truncates to ``max_len`` *bytes*-equivalent characters; the truncation
      is done on the stem so the extension survives.
    - If the resulting stem matches a reserved DOS device name (CON, PRN,
      AUX, NUL, COMn, LPTn), prefixes ``"_"`` to disambiguate.

    A blank input or an input that sanitizes down to nothing returns
    ``"_"`` so the caller always gets a non-empty string.
    """
    if name is None:
        return replacement
    s = _FORBIDDEN_CHARS_RE.sub(replacement, str(name))
    s = s.strip().strip(". ").strip()
    if not s:
        return replacement
    stem, ext = _split_stem_ext(s)
    if stem.upper() in _RESERVED_DEVICE_NAMES:
        stem = "_" + stem
    stem, fallback = _truncate_stem_to_budget(stem, ext, max_len, replacement)
    if fallback is not None:
        return fallback
    return stem + ("." + ext if ext else "")


# Placeholders accepted by the optional download.filename_template.
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def render_filename_template(template: str, fields: dict) -> str:
    """Render ``template`` against ``fields``, ignoring unknown placeholders.

    Unlike ``str.format()``, this never raises on missing keys — unknown
    placeholders are left in the output as-is so the user notices the
    typo without crashing the download.
    """
    if not template:
        return ""

    def _replace(match):
        key = match.group(1)
        if key in fields:
            value = fields[key]
            return "" if value is None else str(value)
        return match.group(0)

    return _TEMPLATE_PLACEHOLDER_RE.sub(_replace, template)
