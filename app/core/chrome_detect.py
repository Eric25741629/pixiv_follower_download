"""Detect the locally installed Chrome version and build a matching User-Agent string."""
from __future__ import annotations
import os
import re


def detect_chrome_ua() -> str | None:
    """Return a Chrome User-Agent string matching the installed Chrome, or None."""
    version = _read_from_registry() or _read_from_appdata()
    if not version:
        return None
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Safari/537.36"
    )


def _read_from_registry() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    candidates = [
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
    ]
    for hive, path in candidates:
        try:
            with winreg.OpenKey(hive, path) as key:
                val, _ = winreg.QueryValueEx(key, "version")
                if isinstance(val, str) and val.strip():
                    return val.strip()
        except OSError:
            continue
    return None


def _read_from_appdata() -> str | None:
    try:
        local = os.environ.get("LOCALAPPDATA", "")
        base = os.path.join(local, "Google", "Chrome", "Application")
        if not os.path.isdir(base):
            return None
        version_pat = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
        versions = [d for d in os.listdir(base) if version_pat.match(d)]
        if not versions:
            return None
        versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)
        return versions[0]
    except Exception:
        return None
