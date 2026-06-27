"""Minimal i18n: JSON locale files + ``t()`` lookup with fallback.

stdlib only. Process-global current locale (set once at startup; apply-on-
restart, never live-switched). Lookup chain: current -> BASE_LOCALE -> key.
``t()`` never raises: a missing key returns the key, a bad ``.format`` returns
the unformatted template.
"""
from __future__ import annotations

import json
from pathlib import Path

_LOCALES_DIR = Path(__file__).parent / "locales"
BASE_LOCALE = "zh-TW"

_current = BASE_LOCALE
_cache: dict[str, dict] = {}


def _load(locale: str) -> dict:
    if locale not in _cache:
        path = _LOCALES_DIR / f"{locale}.json"
        try:
            _cache[locale] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache[locale] = {}
    return _cache[locale]


def available_locales() -> list[str]:
    return sorted(p.stem for p in _LOCALES_DIR.glob("*.json"))


def set_locale(code: str | None) -> None:
    """Set the process-global locale. Unknown / None -> BASE_LOCALE."""
    global _current
    _current = code if code in available_locales() else BASE_LOCALE


def get_locale() -> str:
    return _current


def t(key: str, **kwargs) -> str:
    val = _load(_current).get(key)
    if val is None and _current != BASE_LOCALE:
        val = _load(BASE_LOCALE).get(key)
    if val is None:
        val = key
    if kwargs:
        try:
            return val.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return val
    return val
