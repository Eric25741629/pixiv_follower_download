from pathlib import Path
import sys
import queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pixiv_thread import download_thread


def _build_thread_stub(tmp_path: Path):
    t = download_thread.__new__(download_thread)
    t._q = queue.Queue()
    t.jxl_enable = True
    t.jxl_delete_original = False
    t.jxl_effort = 7
    t._jxl_path_warned = False
    t._jxl_ok_count = 0
    t._jxl_fail_count = 0
    t._jxl_gif_skip_warned = False
    cjxl = tmp_path / "cjxl.exe"
    cjxl.write_bytes(b"")
    t.jxl_cjxl_path = str(cjxl)
    return t


def test_jxl_jpg_fallback_to_temp_ascii_path(tmp_path: Path):
    t = _build_thread_stub(tmp_path)
    src = tmp_path / "long_中文_檔名.jpg"
    src.write_bytes(b"jpg")
    dst = src.with_suffix(".jxl")

    t._run_cjxl_once = lambda _s, _d: (False, "Getting pixel data failed.")

    def _temp_ok(_s, _d):
        Path(_d).write_bytes(b"jxl")
        return True, ""

    t._run_cjxl_with_temp_ascii_path = _temp_ok
    t._convert_gif_first_frame_then_cjxl = lambda _s, _d: (_ for _ in ()).throw(RuntimeError("should not call"))

    t._convert_file_to_jxl(str(src))

    assert dst.is_file()
    assert t._jxl_ok_count == 1
    assert t._jxl_fail_count == 0


def test_jxl_gif_keeps_animation_path_and_fails_cleanly(tmp_path: Path):
    t = _build_thread_stub(tmp_path)
    src = tmp_path / "動畫_中文.gif"
    src.write_bytes(b"gif")
    dst = src.with_suffix(".jxl")

    t._run_cjxl_once = lambda _s, _d: (False, "Getting pixel data failed.")
    called = {"temp_name": None}

    def _gif_temp_fail(_s, _d, temp_name=None):
        called["temp_name"] = temp_name
        return False, "temp retry failed"

    t._run_cjxl_with_temp_ascii_path = _gif_temp_fail

    t._convert_file_to_jxl(str(src))

    assert not dst.is_file()
    assert t._jxl_ok_count == 0
    assert t._jxl_fail_count == 1
    assert called["temp_name"] == "1_PID.gif"
