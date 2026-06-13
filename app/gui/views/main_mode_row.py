"""Source / scope / combined mode-pill subsystem for ``MainView``
(file-size refactor).

The 來源（抓追隨／抓收藏）/ 範圍（公開／非公開／全部）/ 邊查邊下 mode pills,
their settings persistence, and the step-card relabel/merge logic — mixed into
``MainView`` via ``_MainModeRowMixin``. Every method uses only ``self.`` for
cross-method calls (resolved through inheritance) plus the module-level names
below, so behavior is byte-for-byte identical to the originals. Controls these
methods read/write (``self._mode_row``, ``self._scope_row``,
``self._step_card_texts``, ``self._step_cards``, ``self._safe_update`` …) are
created in ``MainView.__init__`` / live on the concrete class.

``STEP_LABELS`` / ``_BOOKMARK_STEP_LABELS`` / ``_MERGED_STEP3_LABEL`` /
``_SOURCE_TOOLTIPS`` / ``_settings_base_path`` live here (the mode logic owns
them) and are re-exported by ``main_view`` for the few non-mode call sites
(``_make_step_card``) and the tests that import them from ``main_view``.
"""
from __future__ import annotations

import contextlib

from app.gui.glass import current_theme, glass_pill

STEP_LABELS = ["步驟 1\n抓追蹤", "步驟 2\n抓 PID", "步驟 3\n抓 URL", "步驟 4\n下載"]
_BOOKMARK_STEP_LABELS = ["步驟 1\n略過追隨", "步驟 2\n抓收藏 PID"]

# When 邊查邊下 (download.combined_mode) is on, step 3 absorbs step 4: the
# step-3 card is relabeled and the step-4 card is hidden (see
# MainView.apply_combined_mode).
_MERGED_STEP3_LABEL = "步驟 3+4\n邊查邊下"

# 模式說明（舊 _mode_subtitle 文案）改為來源 pill 的 tooltip。
_SOURCE_TOOLTIPS = {
    "following": "維持原本流程：抓追蹤畫師，再掃描畫師作品 PID。",
    "bookmarks": "步驟 2 會掃描你按過愛心的作品 PID，後續抓 URL / 下載流程不變。",
}


def _settings_base_path() -> str:
    import os
    path = os.getenv("APPDATA") + r"/pixiv_download/"
    os.makedirs(path, exist_ok=True)
    return path


class _MainModeRowMixin:
    """Source/scope/combined mode pills + persistence, mixed into ``MainView``."""

    # ------------------------------------------------------------------
    # Combined mode (邊查邊下) — merge step 3+4 cards into one
    # ------------------------------------------------------------------

    @staticmethod
    def _read_combined_mode_setting() -> bool:
        """Read download.combined_mode from settings (best-effort)."""
        try:
            import os
            from app.core.settings_store import SettingsStore
            base = os.getenv("APPDATA") + r"/pixiv_download/"
            return bool(
                SettingsStore(base).get_section("download").get("combined_mode", False)
            )
        except Exception:
            return False

    def apply_combined_mode(self, enabled: bool) -> None:
        """Merge step 3+4 into a single card when *enabled*.

        Step 3's card is relabeled 「步驟 3+4 邊查邊下」 and the step-4 card is
        hidden (the Row reflows). When disabled both revert to the default
        4-card layout. Step-state colors are untouched — combined mode still
        runs as step 3 (card index 2), so progress / done coloring is correct,
        and clicking the merged card still launches step 3 → the combined
        thread via the store flag.
        """
        self._combined_mode = bool(enabled)
        self._step_card_texts[2].value = (
            _MERGED_STEP3_LABEL if enabled else STEP_LABELS[2]
        )
        self._step_cards[3].visible = not enabled
        for ctrl in (self._step_card_texts[2], self._step_cards[3]):
            try:
                ctrl.update()
            except Exception:
                pass

    def refresh_combined_mode(self) -> None:
        """Re-read the setting and re-apply the merged / normal card layout.

        Called when the user returns to the 主頁 tab so a toggle made in the
        settings page is reflected without an app restart.
        """
        self.apply_combined_mode(self._read_combined_mode_setting())

    @staticmethod
    def _read_source_settings() -> tuple[str, str, str]:
        """Read download.source_mode and source privacy scopes from settings."""
        try:
            from app.core.settings_store import SettingsStore
            dl = SettingsStore(_settings_base_path()).get_section("download")
            return (
                str(dl.get("source_mode", "following") or "following"),
                str(dl.get("following_scope", "all") or "all"),
                str(dl.get("bookmark_scope", "all") or "all"),
            )
        except Exception:
            return "following", "all", "all"

    def apply_source_mode(self, mode: str, scope: str = "all") -> None:
        """Paint the main-page source-mode pills and step labels."""
        mode = "bookmarks" if str(mode) == "bookmarks" else "following"
        scope = self._normalize_scope(scope)
        self._source_mode = mode
        self._active_scope = scope
        if mode == "bookmarks":
            self._bookmark_scope = scope
            self._scope_label.value = "收藏範圍"
            self._scope_row.visible = True
            self._step_card_texts[0].value = _BOOKMARK_STEP_LABELS[0]
            self._step_card_texts[1].value = _BOOKMARK_STEP_LABELS[1]
        else:
            self._following_scope = scope
            self._scope_label.value = "追隨範圍"
            self._scope_row.visible = True
            self._step_card_texts[0].value = STEP_LABELS[0]
            self._step_card_texts[1].value = STEP_LABELS[1]
        self._btn_source_following = self._make_mode_button(
            "抓追隨", mode == "following",
            lambda e: self._on_source_mode_change("following"),
            tooltip=_SOURCE_TOOLTIPS["following"],
        )
        self._btn_source_bookmarks = self._make_mode_button(
            "抓收藏", mode == "bookmarks",
            lambda e: self._on_source_mode_change("bookmarks"),
            tooltip=_SOURCE_TOOLTIPS["bookmarks"],
        )
        self._btn_scope_public = self._make_mode_button(
            "公開", scope == "public", lambda e: self._on_scope_change("public")
        )
        self._btn_scope_private = self._make_mode_button(
            "非公開", scope == "private", lambda e: self._on_scope_change("private")
        )
        self._btn_scope_all = self._make_mode_button(
            "全部", scope == "all", lambda e: self._on_scope_change("all")
        )
        self._source_mode_controls.controls = [
            self._btn_source_following, self._btn_source_bookmarks,
        ]
        self._scope_row.controls = [
            self._scope_label,
            self._btn_scope_public,
            self._btn_scope_private,
            self._btn_scope_all,
        ]
        self._bookmark_scope_row = self._scope_row
        self._safe_update(
            self._mode_row,
            self._source_mode_controls, self._scope_row, self._scope_label,
            self._step_card_texts[0], self._step_card_texts[1],
        )

    def _make_mode_button(self, text: str, active: bool, on_click,
                          tooltip: str | None = None):
        pill = glass_pill(
            text, current_theme(self._page), primary=active, on_click=on_click
        )
        if tooltip:
            pill.tooltip = tooltip
        return pill

    @staticmethod
    def _normalize_scope(scope: str) -> str:
        scope = str(scope or "all")
        return scope if scope in {"public", "private", "all"} else "all"

    def refresh_source_mode(self) -> None:
        mode, following_scope, bookmark_scope = self._read_source_settings()
        self._following_scope = self._normalize_scope(following_scope)
        self._bookmark_scope = self._normalize_scope(bookmark_scope)
        active_scope = self._bookmark_scope if mode == "bookmarks" else self._following_scope
        self.apply_source_mode(mode, active_scope)

    def _persist_source_settings(self, fields: dict) -> None:
        with contextlib.suppress(Exception):
            from app.core.settings_store import SettingsStore
            SettingsStore(_settings_base_path()).update_fields("download", fields)

    def _on_source_mode_change(self, mode: str) -> None:
        self._persist_source_settings({"source_mode": mode})
        mode = "bookmarks" if str(mode) == "bookmarks" else "following"
        scope = self._bookmark_scope if mode == "bookmarks" else self._following_scope
        self.apply_source_mode(mode, scope)

    def _on_scope_change(self, scope: str) -> None:
        scope = self._normalize_scope(scope)
        key = "bookmark_scope" if self._source_mode == "bookmarks" else "following_scope"
        self._persist_source_settings({key: scope})
        self.apply_source_mode(self._source_mode, scope)

    def _on_bookmark_scope_change(self, scope: str) -> None:
        self._on_scope_change(scope)

    def _on_following_scope_change(self, scope: str) -> None:
        self._on_scope_change(scope)
