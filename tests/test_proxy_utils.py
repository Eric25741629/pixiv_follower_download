import pytest
from app.core.proxy_utils import parse_proxy_url, to_requests_proxies, parse_proxy_list


def test_parse_http():
    assert parse_proxy_url("http://1.2.3.4:8080") == "http://1.2.3.4:8080"


def test_parse_https():
    assert parse_proxy_url("https://1.2.3.4:443") == "https://1.2.3.4:443"


def test_parse_socks5():
    assert parse_proxy_url("socks5://1.2.3.4:1080") == "socks5://1.2.3.4:1080"


def test_parse_socks5h():
    assert parse_proxy_url("socks5h://1.2.3.4:1080") == "socks5h://1.2.3.4:1080"


def test_parse_socks4():
    assert parse_proxy_url("socks4://1.2.3.4:1080") == "socks4://1.2.3.4:1080"


def test_parse_with_auth():
    url = "socks5://user:pass@1.2.3.4:1080"
    assert parse_proxy_url(url) == url


def test_parse_empty_returns_none():
    assert parse_proxy_url("") is None
    assert parse_proxy_url("   ") is None


def test_parse_comment_returns_none():
    assert parse_proxy_url("# this is a comment") is None


def test_parse_invalid_scheme_returns_none():
    assert parse_proxy_url("ftp://1.2.3.4:21") is None


def test_parse_no_host_returns_none():
    assert parse_proxy_url("http://") is None


def test_to_requests_proxies_none():
    assert to_requests_proxies(None) is None


def test_to_requests_proxies_empty_string():
    assert to_requests_proxies("") is None


def test_to_requests_proxies_http():
    result = to_requests_proxies("http://1.2.3.4:8080")
    assert result == {"http": "http://1.2.3.4:8080", "https": "http://1.2.3.4:8080"}


def test_to_requests_proxies_socks5():
    result = to_requests_proxies("socks5://host:1080")
    assert result == {"http": "socks5://host:1080", "https": "socks5://host:1080"}


def test_parse_proxy_list_basic():
    text = "http://1.1.1.1:80\nsocks5://2.2.2.2:1080"
    assert parse_proxy_list(text) == ["http://1.1.1.1:80", "socks5://2.2.2.2:1080"]


def test_parse_proxy_list_strips_comments_and_blanks():
    text = "\n# comment\nhttp://1.1.1.1:80\n\n  \n"
    assert parse_proxy_list(text) == ["http://1.1.1.1:80"]


def test_parse_proxy_list_dedupes():
    text = "http://1.1.1.1:80\nhttp://1.1.1.1:80"
    assert parse_proxy_list(text) == ["http://1.1.1.1:80"]


def test_parse_proxy_list_empty():
    assert parse_proxy_list("") == []
