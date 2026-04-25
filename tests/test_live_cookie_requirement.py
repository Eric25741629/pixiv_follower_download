from pathlib import Path
import json
import os
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import pixiv_api


def _normalize_pid(raw):
    text = str(raw or "").strip()
    if not text:
        return ""
    token = text.rsplit("/", 1)[-1]
    token = token.split("?", 1)[0].split("#", 1)[0].strip()
    out = []
    for ch in token:
        if ch.isdigit():
            out.append(ch)
        else:
            break
    return "".join(out)


def _drop_pid_from_json_dict(path, pid):
    if not os.path.isfile(path):
        return
    for enc in ("utf-8", "utf-8-sig"):
        try:
            with open(path, "r", encoding=enc) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            if str(pid) in data:
                data.pop(str(pid), None)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            return
        except Exception:
            continue


def _parse_expected(text):
    s = str(text or "").strip().lower()
    if s in {"1", "true", "yes", "y"}:
        return True
    if s in {"0", "false", "no", "n"}:
        return False
    if s in {"none", "null", "unknown", "未知"}:
        return None
    return "UNSET"


@pytest.mark.integration
def test_live_cookie_requirement_for_pid():
    raw_pid = os.getenv("PIXIV_TEST_PID", "").strip()
    if not raw_pid:
        pytest.skip("Set PIXIV_TEST_PID to run this live test.")

    pid = _normalize_pid(raw_pid)
    if not pid:
        pytest.fail(f"Invalid PIXIV_TEST_PID: {raw_pid}")

    cookie = os.getenv("PIXIV_TEST_COOKIE", "").strip() or None
    agent = os.getenv("PIXIV_TEST_AGENT", "").strip() or pixiv_api.random_Agent()
    expected = _parse_expected(os.getenv("PIXIV_EXPECT_REQUIRES_COOKIE", ""))

    base = os.path.join(os.getenv("APPDATA") or "", "pixiv_download")
    os.makedirs(base, exist_ok=True)
    _drop_pid_from_json_dict(os.path.join(base, "pixiv_info_cache.json"), pid)
    _drop_pid_from_json_dict(os.path.join(base, "pixiv_cookie_requirement.json"), pid)
    try:
        with pixiv_api._pixiv_info_cache_lock:
            pixiv_api._pixiv_info_cache.pop(str(pid), None)
    except Exception:
        pass

    artwork_url = f"https://www.pixiv.net/artworks/{pid}"
    result = pixiv_api.Pixiv_info(artwork_url, Agent=agent, cookie=cookie)
    requires_cookie = pixiv_api.get_pixiv_cookie_requirement(pid)

    if expected != "UNSET":
        assert (
            requires_cookie == expected
        ), (
            f"PID {pid} requires_cookie expected={expected} actual={requires_cookie} "
            f"result_preview={result}"
        )
    else:
        # no explicit expectation; just ensure test actually produced a tracked result
        assert requires_cookie in (True, False, None), f"Unexpected requires_cookie: {requires_cookie}"

