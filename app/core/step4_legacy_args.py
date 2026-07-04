"""Backward-compatible constructor-argument coercion for ``download_thread``
(file-size refactor).

Pure, stateless translation of the legacy positional / keyword ``__init__``
surface into an ``overrides`` dict. Mixed into ``download_thread`` via
``_Step4LegacyArgsMixin`` so callers/tests keep reaching them as
``download_thread._cast_or_skip`` / ``._apply_legacy_*`` /
``._LEGACY_*_SCHEMA`` through inheritance. Nothing here touches instance
state; every method is static. ``tests/test_apply_legacy_args.py`` pins the
behaviour.
"""
from __future__ import annotations

import contextlib

from app.core.pixiv_thread_base import _normalize_special_like_rules


class _Step4LegacyArgsMixin:
    _LEGACY_POSITIONAL_SCHEMA = [
        ("jxl_enable", bool),
        ("jxl_cjxl_path", lambda v: str(v).strip() or None),
        ("jxl_delete_original", bool),
        ("jxl_effort", int),
    ]
    _LEGACY_SCALAR_KW_SCHEMA = [
        ("jxl_enable", bool),
        ("jxl_cjxl_path", lambda v: str(v).strip() or None),
        ("jxl_delete_original", bool),
        ("jxl_effort", int),
        ("jxl_skip_gif", bool),
        ("like_num", lambda v: int(v or 0)),
        ("r18_like_num", lambda v: int(v or 0)),
        ("ai_gen_dir", bool),
        ("filename_template", lambda v: str(v or "").strip() or None),
        ("tag_strip_brackets", bool),
        ("tag_strip_special_chars", bool),
        ("author_order", bool),
        ("set_file_mtime", bool),
        ("download_deadline_sec", lambda v: float(v) if v else None),
    ]

    @staticmethod
    def _cast_or_skip(caster, raw):
        """Run ``caster(raw)`` returning the value, or ``None`` on any failure / casted None."""
        try:
            value = caster(raw)
        except Exception:
            return None
        return value

    @staticmethod
    def _apply_legacy_positional(args, overrides):
        """Translate positional legacy args into the overrides dict."""
        if not args:
            return
        for idx, (key, caster) in enumerate(_Step4LegacyArgsMixin._LEGACY_POSITIONAL_SCHEMA):
            if idx >= len(args):
                break
            value = _Step4LegacyArgsMixin._cast_or_skip(caster, args[idx])
            if value is not None:
                overrides[key] = value

    @staticmethod
    def _apply_legacy_scalar_kwargs(kwargs, overrides):
        """Translate scalar keyword legacy args into the overrides dict."""
        for key, caster in _Step4LegacyArgsMixin._LEGACY_SCALAR_KW_SCHEMA:
            if key not in kwargs:
                continue
            value = _Step4LegacyArgsMixin._cast_or_skip(caster, kwargs[key])
            if value is not None:
                overrides[key] = value

    @staticmethod
    def _apply_legacy_list_kwargs(kwargs, overrides):
        """Pass-through list kwargs (ban_tag / must_tag) when shaped correctly."""
        for list_key in ("ban_tag", "must_tag"):
            value = kwargs.get(list_key)
            if isinstance(value, list):
                overrides[list_key] = value

    @staticmethod
    def _apply_legacy_special_like_rules(kwargs, overrides):
        """Run special_like_rules through the normalizer when present."""
        if "special_like_rules" not in kwargs:
            return
        with contextlib.suppress(Exception):
            overrides["special_like_rules"] = _normalize_special_like_rules(
                kwargs.get("special_like_rules", [])
            )

    @staticmethod
    def _apply_legacy_constructor_args(legacy_args, legacy_kwargs):
        """Resolve backward-compatible positional/keyword args to a dict of overrides.

        Silently skips malformed entries so a caller passing junk cannot break __init__.
        """
        overrides = {}
        kwargs = legacy_kwargs or {}
        _Step4LegacyArgsMixin._apply_legacy_positional(legacy_args, overrides)
        _Step4LegacyArgsMixin._apply_legacy_scalar_kwargs(kwargs, overrides)
        _Step4LegacyArgsMixin._apply_legacy_list_kwargs(kwargs, overrides)
        _Step4LegacyArgsMixin._apply_legacy_special_like_rules(kwargs, overrides)
        return overrides
