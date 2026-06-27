import app.i18n as i18n


def setup_function(_fn):
    # Reset process-global locale before each test (module state persists).
    i18n.set_locale(i18n.BASE_LOCALE)


def test_base_locale_hit():
    i18n.set_locale("zh-TW")
    assert i18n.t("nav.home") == "主頁"
    assert i18n.t("appbar.title") == "Pixiv 下載器"


def test_current_locale_overrides_base():
    i18n.set_locale("en")
    assert i18n.t("nav.home") == "Home"


def test_untranslated_key_falls_back_to_base(monkeypatch):
    # en.json now has full key parity (full English coverage), so simulate an
    # untranslated key by dropping it from the loaded en dict: it must fall back
    # to the zh-TW base value rather than showing the raw key.
    en_without = {k: v for k, v in i18n._load("en").items() if k != "settings.lang.restart_hint"}
    monkeypatch.setitem(i18n._cache, "en", en_without)
    i18n.set_locale("en")
    assert i18n.t("settings.lang.restart_hint") == "重啟後生效"


def test_missing_key_returns_key_itself():
    i18n.set_locale("zh-TW")
    assert i18n.t("totally.unknown.key") == "totally.unknown.key"


def test_format_kwargs_on_templated_value():
    i18n.set_locale("zh-TW")
    assert i18n.t("log.skip.like", pid="123456", cur=18, need=30) == \
        "略過 PID 123456：未達最低讚數（18 / 30）"


def test_format_missing_kwarg_returns_unformatted():
    i18n.set_locale("zh-TW")
    # Missing the kwargs -> must not raise; returns the unformatted template.
    assert i18n.t("log.skip.like") == "略過 PID {pid}：未達最低讚數（{cur} / {need}）"


def test_available_locales_lists_json_stems_sorted():
    assert i18n.available_locales() == ["en", "zh-TW"]


def test_set_locale_none_falls_back_to_base():
    i18n.set_locale(None)
    assert i18n.get_locale() == "zh-TW"


def test_set_locale_unknown_falls_back_to_base():
    i18n.set_locale("fr")
    assert i18n.get_locale() == "zh-TW"
    assert i18n.t("nav.home") == "主頁"


def test_all_wired_keys_exist_in_base_locale():
    # Every key the app references at build time MUST exist in the base locale
    # so the UI never shows a raw dotted key. (en may omit -> falls back.)
    import json
    from pathlib import Path
    wired = {
        "nav.home", "nav.settings", "nav.cookies", "nav.stats",
        "appbar.title", "theme.toggle_tooltip",
        "settings.title", "settings.section.interface",
        "settings.lang.label", "settings.lang.restart_hint",
    }
    base = json.loads(
        (Path(i18n.__file__).parent / "locales" / "zh-TW.json").read_text(encoding="utf-8")
    )
    missing = wired - base.keys()
    assert not missing, f"keys missing from zh-TW.json: {missing}"
