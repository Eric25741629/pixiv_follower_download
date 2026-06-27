import datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.live_settings import LiveSettings
from app.core.settings_store import SettingsStore
from app.core.thread_download import download_thread


def _write_settings(base, **sections):
    store = SettingsStore(str(base))
    data = store.load()
    for section, values in sections.items():
        data[section].update(values)
    store.save(data)


def _thread(tmp_path):
    t = download_thread.__new__(download_thread)
    t._live = LiveSettings(str(tmp_path))
    t._live_sig = None
    t.like_num = 0
    t.ban_tag = []
    t.must_tag = []
    t.special_like_rules = []
    t._ban_tag_norm = []
    t._must_tag_norm = []
    t._pid_filter_decision = {"111": (True, "pass")}
    t.download_path = str(tmp_path / "old")
    t.create_dir = False
    t.no_R18G_dir = True
    t.no_R18_dir = True
    t.ai_gen_dir = False
    t.nogif = False
    t.notag = False
    t.notime = False
    t.download_time = datetime.datetime(1970, 1, 1)
    t.intra_pid_wait_min = 1
    t.intra_pid_wait_max = 3
    t.jxl_enable = False
    t.jxl_effort = 7
    t.jxl_delete_original = False
    t.jxl_cjxl_path = "old-cjxl"
    t._legacy_pid_cooldown_avg = 35
    t.filename_template = ""
    t.tag_strip_brackets = False
    t.tag_strip_special_chars = False
    t.allurl = ["https://i.pximg.net/img-original/img/1/111_p0.jpg"]
    return t


def test_step4_apply_live_settings_refreshes_filters_paths_waits_and_jxl(tmp_path):
    new_path = tmp_path / "new-downloads"
    _write_settings(
        tmp_path,
        download={
            "path": str(new_path),
            "like_num": 10,
            "ban_tag": ["R-18"],
            "must_tag": ["cat"],
            "download_time": "2024-01-02 03:04:05",
            "filename_template": "{pid}.{ext}",
            "tag_strip_brackets": True,
            "tag_strip_special_chars": True,
        },
        filter={"nogif": True, "notag": True, "notime": True},
        directory={
            "create_dir": False,
            "no_R18G_dir": False,
            "no_R18_dir": False,
            "ai_gen_dir": True,
        },
        performance={
            "intra_pid_wait_min": 4,
            "intra_pid_wait_max": 2,
            "pid_cooldown_avg": 88,
        },
        jxl={
            "enable": True,
            "effort": 99,
            "delete_original": True,
            "cjxl_path": "C:/missing/cjxl.exe",
        },
    )
    t = _thread(tmp_path)

    t._apply_live_settings_if_changed()

    assert t.like_num == 10
    assert t.ban_tag == ["R-18"]
    assert t._ban_tag_norm == ["r-18"]
    assert t.must_tag == ["cat"]
    assert t._must_tag_norm == ["cat"]
    assert t._pid_filter_decision == {}
    assert t.download_path == str(new_path)
    assert t.nogif is True
    assert t.notag is True
    assert t.notime is True
    assert t.download_time == datetime.datetime(2024, 1, 2, 3, 4, 5)
    assert t.intra_pid_wait_min == 4
    assert t.intra_pid_wait_max == 4
    assert t._legacy_pid_cooldown_avg == 88
    assert t.jxl_enable is True
    assert t.jxl_effort == 9
    assert t.jxl_delete_original is True
    assert t.filename_template == "{pid}.{ext}"
    assert t.tag_strip_brackets is True
    assert t.tag_strip_special_chars is True


def test_step4_lowering_like_num_does_not_grow_existing_queue(tmp_path):
    _write_settings(tmp_path, download={"like_num": 1})
    t = _thread(tmp_path)
    t.allurl = ["https://i.pximg.net/img-original/img/1/111_p0.jpg"]
    t.like_num = 1000

    t._apply_live_settings_if_changed()

    assert t.like_num == 1
    assert t.allurl == ["https://i.pximg.net/img-original/img/1/111_p0.jpg"]


def test_step4_resolve_download_target_dir_uses_refreshed_download_path(tmp_path):
    new_path = tmp_path / "fresh"
    _write_settings(tmp_path, download={"path": str(new_path)}, directory={"create_dir": False})
    t = _thread(tmp_path)

    t._apply_live_settings_if_changed()
    target = t._resolve_download_target_dir([], "123")

    assert target == str(new_path)
    assert new_path.exists()


def test_step4_live_disable_ai_generated_directory_routes_to_download_root(tmp_path):
    _write_settings(tmp_path, directory={"create_dir": False, "ai_gen_dir": False})
    t = _thread(tmp_path)
    t.ai_gen_dir = True

    t._apply_live_settings_if_changed()
    target = t._resolve_download_target_dir(["AI生成"], "123")

    assert t.ai_gen_dir is False
    assert Path(target) == tmp_path / "old"
    assert not (tmp_path / "old" / "AI生成").exists()
