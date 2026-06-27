from __future__ import annotations
from urllib.parse import urlparse

_SUPPORTED_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4")


def parse_proxy_url(raw: str) -> str | None:
    """Normalize a single proxy URL string. Returns None for empty or bad input."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s.startswith("#"):
        return None
    try:
        parsed = urlparse(s)
    except Exception:
        return None
    if parsed.scheme not in _SUPPORTED_SCHEMES:
        return None
    if not parsed.hostname:
        return None
    return s


def to_requests_proxies(proxy_url: str | None) -> dict | None:
    """Convert a pre-validated proxy URL to a requests-compatible ``proxies`` dict.

    The caller is expected to have run the URL through ``parse_proxy_url`` first
    (or to have constructed ``proxy_url`` from a trusted source). This function
    does not re-validate the URL; an invalid URL will produce a dict that fails
    at request time, not at parse time.

    Returns ``None`` for direct connection (no proxy).
    """
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def parse_proxy_list(text: str) -> list[str]:
    """Parse a multiline proxy list, stripping blank lines and ``#`` comments.

    Deduplicates while preserving order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parsed = parse_proxy_url(stripped)
        if parsed and parsed not in seen:
            seen.add(parsed)
            result.append(parsed)
    return result


def test_proxy(proxy_url: str | None, timeout: int = 10) -> tuple[bool, str]:
    """Synchronously test a proxy by GETing https://www.pixiv.net.

    Returns (success, message). On success the message includes the HTTP
    status. On failure it includes the truncated exception text.
    """
    import requests
    try:
        proxies = to_requests_proxies(proxy_url)
        resp = requests.get(
            "https://www.pixiv.net",
            proxies=proxies,
            timeout=timeout,
            verify=True,
            allow_redirects=True,
        )
        return True, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)[:80]
