"""Download-filename composition for ``download_thread`` (file-size refactor).

Pure tag-cleanup + filename-rendering helpers, mixed into ``download_thread``
via ``_FilenameMixin``. Only ``_normalize_tag_for_filename`` /
``_build_hashtag_text`` read instance state (the two ``tag_strip_*`` flags);
the rest are static. The module-level regexes compile once on import.

The genuinely-invisible codepoints (zero-width chars in ``_ZERO_WIDTH_RE`` and
the Variation Selectors inside ``_DECORATIVE_CHARS_RE``) are written with
explicit ``\\u`` escapes so the source stays byte-stable; the escapes match the
documented Unicode blocks one-for-one. ``tests/test_normalize_tag_for_filename.py``
pins every class down.
"""
from __future__ import annotations

import re

# Tag-cleanup regexes used by _normalize_tag_for_filename. Kept at module level
# so they compile once on import.
#
# _ZERO_WIDTH_RE: zero-width / invisible chars that take up no display width
# but still consume filename-length budget. Always stripped (no toggle).
#   U+200B-200D zero-width space / non-joiner / joiner
#   U+FEFF     byte-order mark / zero-width no-break space
#   U+00AD     soft hyphen
_ZERO_WIDTH_RE = re.compile("[​-‍﻿­]")

# _BRACKET_CONTENT_RE: matched bracket pairs with their content. Each
# alternative uses a tight negated char class so we only consume the innermost
# pair — a second pass over the same string mops up any outer pair that became
# empty after the inner content vanished. Order: ASCII (), full-width （）,
# ASCII [], CJK 【】《》〈〉「」『』〔〕〘〙. Unmatched single-side brackets
# are intentionally left alone so `R-18(警告` stays readable.
_BRACKET_CONTENT_RE = re.compile(
    r"(?:"
    r"\([^()]*\)"
    r"|（[^（）]*）"
    r"|\[[^\[\]]*\]"
    r"|【[^【】]*】"
    r"|《[^《》]*》"
    r"|〈[^〈〉]*〉"
    r"|「[^「」]*」"
    r"|『[^『』]*』"
    r"|〔[^〔〕]*〕"
    r"|〘[^〘〙]*〙"
    r")"
)

# _DECORATIVE_CHARS_RE: Unicode-range blocklist for the
# tag_strip_special_chars toggle. Covers arrows, geometric shapes, misc
# symbols (★ ♀ ♪ etc.), dingbats, and the full emoji span U+1F000-U+1FAFF.
# Also strips Variation Selectors U+FE00-U+FE0F — these combine with a base
# character to switch between text-style and emoji-style rendering (e.g.
# "♪️"). If we strip the base ♪ but leave the VS16 selector behind,
# the filename ends up with a stray invisible codepoint, so VS selectors
# must go alongside the emoji they decorate.
# Deliberately excludes the CJK Symbols and Punctuation block (U+3000-U+303F)
# because that overlaps with the bracket characters handled by the brackets
# toggle; if special_chars is on but brackets is off, we still want brackets
# preserved. Extend by adding more ranges to this character class.
_DECORATIVE_CHARS_RE = re.compile(
    "["
    "←-⇿"   # Arrows
    "⌀-⏿"   # Misc Technical
    "①-⓿"   # Enclosed Alphanumerics
    "─-▟"   # Box Drawing + Block Elements
    "■-◿"   # Geometric Shapes (◯ ● ◎ ◇ ◆)
    "☀-⛿"   # Misc Symbols (★ ☆ ♀ ♂ ♪ ♫)
    "✀-➿"   # Dingbats (✂ ✈ ✏)
    "⬀-⯿"   # Misc Symbols and Arrows
    "︀-️"  # Variation Selectors (VS1-VS16, esp. U+FE0F)
    r"\U0001F000-\U0001FAFF"  # All emoji blocks
    "※〝〞〽"  # explicit additions outside the ranges above
    "]"
)


class _FilenameMixin:
    """Download-filename / tag-cleanup helpers, mixed into ``download_thread``."""

    def _normalize_tag_for_filename(self, raw_tag):
        """Clean one tag for inclusion in the {hashtag} filename segment.

        Pipeline (in order):
          1. Strip zero-width / invisible chars (always on — they have no
             display value but eat filename budget).
          2. Optionally strip matched-bracket pairs and their content
             (``tag_strip_brackets`` toggle). Two passes so newly-empty outer
             pairs after the inner content disappears also get cleaned.
          3. Optionally strip decorative symbols and the entire emoji range
             (``tag_strip_special_chars`` toggle).
          4. Collapse any remaining empty-bracket pairs that survived steps
             1-3 (cosmetic — Pixiv sometimes ships ``tag（）`` shapes).
          5. Unify all whitespace variants (full-width, no-break, etc.) into
             single ASCII space and trim.
        """
        text = str(raw_tag or "").strip()
        if not text:
            return ""
        # 1. Zero-width / invisible chars — drop entirely.
        text = _ZERO_WIDTH_RE.sub("", text)
        # 2. Bracket-content stripping (toggle).
        if getattr(self, "tag_strip_brackets", False):
            # Two passes: nested brackets like ((a)(b)) → ((a)(b)) loses the
            # innermost first; the second sub picks up the now-empty outer.
            text = _BRACKET_CONTENT_RE.sub("", text)
            text = _BRACKET_CONTENT_RE.sub("", text)
        # 3. Decorative symbols / emoji (toggle).
        if getattr(self, "tag_strip_special_chars", False):
            text = _DECORATIVE_CHARS_RE.sub("", text)
        # 4. Mop up empty bracket pairs left behind by translation/cleanup.
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r"（\s*）", "", text)
        text = re.sub(r"\[\s*\]", "", text)
        text = re.sub(r"【\s*】", "", text)
        # 5. Whitespace unification: \s matches 　 (full-width space) and
        #   (no-break space) in Python 3, so a single \s+ collapse here
        # handles all width variants the user encounters in Pixiv tags.
        text = re.sub(r"\s+", " ", text).strip()
        text = text.strip(" _-")
        return text

    def _build_hashtag_text(self, tags, max_len=230):
        if not isinstance(tags, list):
            return " "
        out = []
        current_len = 0
        for many in tags:
            token = self._normalize_tag_for_filename(many)
            if not token:
                continue
            # Keep compatibility with old format: each tag separated by one space.
            extra = len(token) + (1 if out else 0)
            if current_len + extra > int(max_len):
                break
            out.append(token)
            current_len += extra
        if not out:
            # Keep legacy behavior: preserve one leading space even when no tag.
            return " "
        # Keep legacy behavior: two leading spaces before first tag.
        return "  " + " ".join(out)

    @staticmethod
    def _split_timetag(timetag, notime):
        """Return (date, time) parts of a 'YYYYMMDD_HHMMSS' tag, or empty pair when suppressed."""
        if not timetag or notime or "_" not in timetag:
            return "", ""
        date_part, time_part = timetag.split("_", 1)
        return date_part, time_part

    @staticmethod
    def _filename_template_fields(pid, page_suffix, ext, hashtag, timetag, notag, notime):
        """Build the placeholder dict consumed by render_filename_template."""
        date_part, time_part = _FilenameMixin._split_timetag(timetag, notime)
        return {
            "pid": str(pid),
            "page": page_suffix or "",
            "ext": ext,
            "hashtag": hashtag if not notag else "",
            "tag": (hashtag.strip() if hashtag and not notag else ""),
            "timetag": timetag if not notime else "",
            "date": date_part,
            "time": time_part,
        }

    @staticmethod
    def _render_template_filename(template, ext, fields):
        """Render the user template, append ext if missing, sanitize the result."""
        from app.core.filename_utils import render_filename_template, sanitize_filename
        rendered = render_filename_template(template, fields)
        if not rendered.lower().endswith("." + str(ext).lower()):
            rendered = rendered + "." + ext
        return sanitize_filename(rendered)

    @staticmethod
    def _render_default_filename(pid, page_suffix, ext, hashtag, timetag, notag, notime):
        """Render the default '[timetag_]PID{pid}{page_suffix}[{hashtag}].{ext}' layout."""
        from app.core.filename_utils import sanitize_filename
        parts = []
        if not notime and timetag:
            parts.append(timetag)
        core = 'PID' + str(pid) + (page_suffix or '')
        if not notag:
            core += hashtag
        parts.append(core)
        return sanitize_filename('_'.join(parts) + '.' + ext)

    @staticmethod
    def _build_download_filename(pid, *, page_suffix, ext, hashtag, timetag, notag, notime,
                                 template=""):
        """Compose a download filename and sanitize it for filesystem safety.

        Default layout: "[timetag_]PID{pid}{page_suffix}[{hashtag}].{ext}".
        When ``template`` is non-empty the user template is rendered instead,
        with placeholders {pid} {page} {ext} {tag} {hashtag} {timetag} {date} {time}.
        notag / notime flags still gate the {hashtag} and {timetag} placeholders
        to preserve user intent.

        The result is always passed through filename_utils.sanitize_filename.
        """
        tmpl = str(template or "").strip()
        if tmpl:
            fields = _FilenameMixin._filename_template_fields(
                pid, page_suffix, ext, hashtag, timetag, notag, notime,
            )
            return _FilenameMixin._render_template_filename(tmpl, ext, fields)
        return _FilenameMixin._render_default_filename(
            pid, page_suffix, ext, hashtag, timetag, notag, notime,
        )
