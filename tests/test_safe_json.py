"""Unit tests for safe_json."""
from app.core.pixiv_thread_utils import safe_json


class _FakeRes:
    def __init__(self, payload=None, raise_on_json=False):
        self._payload = payload
        self._raise = raise_on_json

    def json(self):
        if self._raise:
            raise ValueError("not JSON")
        return self._payload


def test_walks_nested_keys():
    res = _FakeRes({"body": {"users": [1, 2, 3]}})
    assert safe_json(res, "body", "users") == [1, 2, 3]


def test_returns_default_when_key_missing():
    res = _FakeRes({"body": {}})
    assert safe_json(res, "body", "users", default=[]) == []


def test_returns_default_on_pixiv_error_envelope():
    res = _FakeRes({"error": True, "message": "Cookie expired"})
    assert safe_json(res, "body", "users", default=[]) == []


def test_returns_default_when_response_not_json():
    res = _FakeRes(raise_on_json=True)
    assert safe_json(res, "body", "users", default=[]) == []


def test_returns_full_payload_when_no_keys():
    res = _FakeRes({"foo": 1})
    assert safe_json(res) == {"foo": 1}


def test_intermediate_value_not_dict_returns_default():
    res = _FakeRes({"body": "not-a-dict"})
    assert safe_json(res, "body", "users", default=[]) == []


def test_default_none_is_default_default():
    res = _FakeRes({"body": {}})
    assert safe_json(res, "body", "users") is None
