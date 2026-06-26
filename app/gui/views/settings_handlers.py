"""Autosave + cooldown two-way-bind handlers for ``SettingsView``
(file-size refactor).

The per-switch immediate-persist wiring (主動紀錄) and the cooldown
slider/text-field two-way binding + hint label — mixed into ``SettingsView``
via ``_SettingsHandlersMixin``. Every method uses only ``self.`` for
cross-method calls and reads instance controls created in
``SettingsView.__init__`` (``self._tf_cooldown``, ``self._sl_cooldown``,
``self._label_cooldown_hint``, ``self._n_cookies`` …), so behavior is
byte-for-byte identical to the originals. The autosave handler's inner closure
captures only method-local + ``self`` state (no module-level closure variables),
so moving the whole method is safe.

``_store`` (the SettingsStore factory) is imported lazily inside the methods
that persist/read — it lives in ``app.gui.views.settings_view`` and a
module-level import here would form a cycle (settings_view imports this mixin).
The function-local import resolves at call time, after both modules are loaded,
so it is cycle-safe.
"""
from __future__ import annotations

import contextlib

import flet as ft

from app.gui.glass import current_theme


class _SettingsHandlersMixin:
    """Per-switch autosave + cooldown binding, mixed into ``SettingsView``."""

    def _cooldown_hint_color(self, avg: int) -> str:
        theme = current_theme(self._page)
        return theme.warning if avg < 30 else theme.text_secondary

    # ------------------------------------------------------------------
    # Auto-save (per-switch)
    # ------------------------------------------------------------------

    def _switch_autosave_map(self):
        """(switch, section, key, invert) for every persisted toggle.

        invert=True stores the logical negation: the R-18 / R-18G dir
        switches read as "建立分類資料夾" but persist as ``no_*_dir``.
        """
        return [
            (self._sw_hidefollow, "filter", "hidefollow", False),
            (self._sw_nogif, "filter", "nogif", False),
            (self._sw_notag, "filter", "notag", False),
            (self._sw_notime, "filter", "notime", False),
            (self._sw_set_file_mtime, "download", "set_file_mtime", False),
            (self._sw_tag_strip_brackets, "download", "tag_strip_brackets", False),
            (self._sw_tag_strip_special_chars, "download", "tag_strip_special_chars", False),
            (self._sw_author_order, "download", "author_order", False),
            (self._sw_combined_mode, "download", "combined_mode", False),
            (self._sw_force_rescan, "download", "force_full_rescan", False),
            (self._sw_schedule_enabled, "schedule", "enabled", False),
            (self._sw_create_dir, "directory", "create_dir", False),
            (self._sw_r18_dir, "directory", "no_R18_dir", True),
            (self._sw_r18g_dir, "directory", "no_R18G_dir", True),
            (self._sw_ai_dir, "directory", "ai_gen_dir", False),
            (self._sw_jxl, "jxl", "enable", False),
            (self._sw_jxl_delete, "jxl", "delete_original", False),
            (self._sw_jxl_skip_gif, "jxl", "skip_gif", False),
            (self._sw_single_thread, "performance", "single_thread_mode", False),
        ]

    def _wire_switch_autosave(self) -> None:
        for switch, section, key, invert in self._switch_autosave_map():
            switch.on_change = self._make_autosave_handler(switch, section, key, invert)

    def _make_autosave_handler(self, switch, section, key, invert):
        """Persist just *key* in *section* whenever *switch* is toggled.

        Uses update_fields (single-field read-modify-write) so it never
        clobbers unsaved TextField / Slider edits in the same section.
        """
        def _handler(_e) -> None:
            from app.gui.views.settings_view import _store
            value = (not bool(switch.value)) if invert else bool(switch.value)
            with contextlib.suppress(Exception):
                _store().update_fields(section, {key: value})
            if self._on_saved is not None:
                with contextlib.suppress(Exception):
                    self._on_saved()
        return _handler

    # ------------------------------------------------------------------
    # Cooldown helpers
    # ------------------------------------------------------------------

    def _cooldown_hint(self, avg) -> str:
        try:
            avg_f = max(0.0, float(avg))
        except (TypeError, ValueError):
            avg_f = 35.0
        n = self._n_cookies
        throughput = avg_f / n
        suffix = "；推薦 >= 30 秒"
        if n == 1:
            return f"1 個 cookie：每請求約 {throughput:.0f} 秒{suffix}"
        return (f"{n} 個 cookie：單帳號每 {avg_f:.0f} 秒一次，"
                f"整體每請求約 {throughput:.1f} 秒{suffix}")

    def reload_cookie_count(self) -> None:
        """Re-read cookie count from store; refresh hint label in-place."""
        from app.gui.views.settings_view import _store
        auth = _store().get_section("auth")
        self._n_cookies = max(1, len(auth.get("cookies_pool", []) or []))
        avg = self._safe_int_cooldown()
        self._label_cooldown_hint.value = self._cooldown_hint(avg)
        self._label_cooldown_hint.color = self._cooldown_hint_color(avg)
        with contextlib.suppress(Exception):
            self._label_cooldown_hint.update()

    def _on_cooldown_slider_change(self, e: ft.ControlEvent) -> None:
        try:
            val = int(e.control.value)
        except (TypeError, ValueError):
            return
        self._tf_cooldown.value = str(val)
        self._label_cooldown_hint.value = self._cooldown_hint(val)
        self._label_cooldown_hint.color = self._cooldown_hint_color(val)
        try:
            self._tf_cooldown.update()
            self._label_cooldown_hint.update()
        except Exception:
            pass

    def _on_cooldown_tf_change(self, e: ft.ControlEvent) -> None:
        try:
            val = max(0, min(300, int(self._tf_cooldown.value or "35")))
        except (TypeError, ValueError):
            return
        self._tf_cooldown.value = str(val)            # snap displayed text to clamped value
        self._sl_cooldown.value = float(val)
        self._label_cooldown_hint.value = self._cooldown_hint(val)
        self._label_cooldown_hint.color = self._cooldown_hint_color(val)
        try:
            self._tf_cooldown.update()                # flush the TextField update
            self._sl_cooldown.update()
            self._label_cooldown_hint.update()
        except Exception:
            pass

    def _safe_int_cooldown(self) -> int:
        try:
            return max(0, min(300, int(self._tf_cooldown.value or "35")))
        except (TypeError, ValueError):
            return 35
