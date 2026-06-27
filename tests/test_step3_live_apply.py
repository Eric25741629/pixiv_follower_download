from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.live_settings import LiveSettings
from app.core.settings_store import SettingsStore
from app.core.thread_url_fetch import get_img_url_thread


def _write_settings(base, **sections):
    store = SettingsStore(str(base))
    data = store.load()
    for section, values in sections.items():
        data[section].update(values)
    store.save(data)


def test_step3_apply_live_settings_refreshes_filters_waits_and_rescrape(tmp_path):
    _write_settings(
        tmp_path,
        download={
            "like_num": 20,
            "ban_tag": ["blocked"],
            "must_tag": ["required"],
            "rescrape_within_days": 14,
        },
        performance={
            "pid_wait_nocookie_min": 9,
            "pid_wait_nocookie_max": 3,
        },
    )
    t = get_img_url_thread.__new__(get_img_url_thread)
    t._live = LiveSettings(str(tmp_path))
    t._live_sig = None
    t.like_num = 0
    t.ban_tag = []
    t.must_tag = []
    t.special_like_rules = []
    t._ban_tag_norm = []
    t._must_tag_norm = []
    t._pid_filter_decision = {"111": (True, "pass")}
    t.pid_wait_nocookie_min = 1
    t.pid_wait_nocookie_max = 6
    t._rescrape_within_days = 0

    t._apply_live_settings_if_changed()

    assert t.like_num == 20
    assert t.ban_tag == ["blocked"]
    assert t.must_tag == ["required"]
    assert t._ban_tag_norm == ["blocked"]
    assert t._must_tag_norm == ["required"]
    assert t._pid_filter_decision == {}
    assert t.pid_wait_nocookie_min == 9
    assert t.pid_wait_nocookie_max == 9
    assert t._rescrape_within_days == 14
