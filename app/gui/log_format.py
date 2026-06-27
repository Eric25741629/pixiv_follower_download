from __future__ import annotations
import re
import flet as ft

COLOR_MAP: dict[str, str] = {
    "red":    ft.Colors.RED_600,
    "green":  ft.Colors.GREEN_600,
    "gray":   ft.Colors.GREY_600,
    "black":  ft.Colors.ON_SURFACE,
    "orange": ft.Colors.ORANGE_600,
    "blue":   ft.Colors.BLUE_600,
    "teal":   ft.Colors.TEAL_600,
}

_FONT_RE = re.compile(
    r"<font\s+color=['\"](#?\w+)['\"]>(.*?)</font>",
    re.IGNORECASE | re.DOTALL,
)


def html_to_spans(html: str) -> list[ft.TextSpan]:
    """Convert a single HTML log line to a list of ft.TextSpan objects."""
    if not html:
        return []

    text = re.sub(r"^\s*<p>(.*)</p>\s*$", r"\1", html.strip(), flags=re.DOTALL | re.IGNORECASE)

    spans: list[ft.TextSpan] = []
    last_end = 0

    for m in _FONT_RE.finditer(text):
        before = text[last_end:m.start()]
        if before:
            spans.append(ft.TextSpan(text=before))

        color_name = m.group(1).lower()
        content = m.group(2)
        flet_color = COLOR_MAP.get(color_name) or (color_name if color_name.startswith("#") else None)
        style = ft.TextStyle(color=flet_color) if flet_color else None
        spans.append(ft.TextSpan(text=content, style=style))
        last_end = m.end()

    tail = text[last_end:]
    if tail:
        spans.append(ft.TextSpan(text=tail))

    return spans
