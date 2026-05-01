from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pixiv_thread_utils import (
    _extract_pid_candidates_from_name,
    canonicalize_pximg_url_for_storage,
    normalize_pid,
    scan_download_folder_for_pid_set,
    sync_exist_pid_with_download_folder,
)


GIVEN_NAME = "20260101_033742_PID137328754  R-18 うごイラ R-18G スカトロ レオ(ヤリステメスブター)"
GIVEN_PID = "137328754"
PXIMG_URL = "https://i.pximg.net/img-original/img/2025/12/27/02/02/36/139112835-c476a4d067a7b056153ed7a1099abab5_p0.jpg"
PXIMG_FILENAME = "139112835-c476a4d067a7b056153ed7a1099abab5_p0.jpg"
PXIMG_PID = "139112835"
PXIMG_CANONICAL_URL = "https://i.pximg.net/img-original/img/2025/12/27/02/02/36/139112835_p0.jpg"

def test_extract_pid_from_given_filename():
    pids = _extract_pid_candidates_from_name(GIVEN_NAME)
    assert GIVEN_PID in pids


def test_recursive_scan_finds_pid_from_given_filename(tmp_path: Path):
    nested = tmp_path / "nested" / "r18g"
    nested.mkdir(parents=True)
    (nested / f"{GIVEN_NAME}.jpg").write_bytes(b"")

    pids, scanned_files = scan_download_folder_for_pid_set(str(tmp_path), recursive=True)

    assert scanned_files == 1
    assert GIVEN_PID in pids


def test_sync_exist_pid_merges_disk_and_download_folder(tmp_path: Path):
    base_path = tmp_path / "base"
    download_path = tmp_path / "download"
    base_path.mkdir()
    download_path.mkdir()

    # Existing record on disk
    (base_path / "exist_pid.json").write_text('["11111111"]', encoding="utf-8")

    # New downloaded file
    (download_path / f"{GIVEN_NAME}.png").write_bytes(b"")

    result = sync_exist_pid_with_download_folder(
        base_path=str(base_path),
        download_path=str(download_path),
        current_exist_pid=set(),
        recursive=True,
    )

    merged = result["merged_set"]
    assert "11111111" in merged
    assert GIVEN_PID in merged
    assert result["added_from_download"] >= 1


def test_normalize_pid_from_pximg_filename():
    assert normalize_pid(PXIMG_FILENAME) == PXIMG_PID


def test_extract_pid_candidates_from_pximg_filename():
    pids = _extract_pid_candidates_from_name(PXIMG_FILENAME)
    assert PXIMG_PID in pids


def test_canonicalize_pximg_url_for_storage_removes_hash():
    assert canonicalize_pximg_url_for_storage(PXIMG_URL) == PXIMG_CANONICAL_URL


def test_thread_no_use_seleium_propagates_proxy_error():
    """ProxyError from requests.get must propagate so scheduler can disable."""
    import requests
    import queue as _q
    from app.core import thread_pid_scan
    t = thread_pid_scan.get_pixiv_author_imgID_Thread.__new__(
        thread_pid_scan.get_pixiv_author_imgID_Thread
    )
    # Bare-bones init enough to call the method
    t._stop_event = __import__("threading").Event()
    t._pause_event = __import__("threading").Event()
    t._pause_event.set()
    t._q = _q.Queue()

    def boom(*a, **kw):
        raise requests.exceptions.ProxyError("simulated dead proxy")
    t._step2_fetch_artist_pid_list = boom

    import pytest
    with pytest.raises(requests.exceptions.ProxyError):
        t.thread_no_use_seleium_get_pid("cookie", "agent", ".", "1", "777")
