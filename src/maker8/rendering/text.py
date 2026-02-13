"""Render styled text to RGBA numpy arrays using Pillow.

This module is the only place in the project that deals with font loading,
text wrapping, stroke drawing, and alignment.  The ``layers`` module calls
``render_text_image()`` and wraps the result in a MoviePy ``ImageClip``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from maker8.models.spec import TextStyle
from maker8.utils.color import hex_to_rgba

# ── Font cache ───────────────────────────────────────────────────────────────

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

_BUILTIN_FONTS: dict[str, str | None] = {
    "font:inter:regular": None,  # fall back to Pillow default
    "font:inter:bold": None,
}


def _load_font(ref: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return a Pillow font for the given ``font_ref`` and pixel *size*."""
    key = (ref, size)
    if key in _font_cache:
        return _font_cache[key]

    path = _BUILTIN_FONTS.get(ref)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    if path and Path(path).exists():
        font = ImageFont.truetype(path, size)
    else:
        # Try the reference as a direct filesystem path
        if Path(ref).exists():
            font = ImageFont.truetype(ref, size)
        else:
            font = ImageFont.load_default(size)

    _font_cache[key] = font
    return font



# ── Text wrapping ────────────────────────────────────────────────────────────


def _wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """Word-wrap *text* so each line fits within *max_width* pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


# ── Public API ───────────────────────────────────────────────────────────────


def render_text_image(
    text: str,
    width: int,
    height: int,
    style: TextStyle,
    text_align: str = "left",
    valign: str = "top",
) -> np.ndarray[Any, Any]:
    """Return an RGBA ``numpy`` array of size ``(height, width, 4)``."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font = _load_font(style.font_ref, style.size)
    fill = hex_to_rgba(style.color)
    stroke_fill = hex_to_rgba(style.stroke_color) if style.stroke_color else None

    # Wrap
    if style.wrap:
        lines = _wrap_text(text, font, width, draw)
    else:
        lines = [text]

    line_h = style.size * style.line_height
    total_h = line_h * len(lines)

    # Vertical offset
    if valign == "center":
        y_start = (height - total_h) / 2
    elif valign == "bottom":
        y_start = height - total_h
    else:
        y_start = 0.0

    for i, line in enumerate(lines):
        y = y_start + i * line_h
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]

        if text_align == "center":
            x = (width - text_w) / 2
        elif text_align == "right":
            x = float(width - text_w)
        else:
            x = 0.0

        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_fill=stroke_fill,
            stroke_width=style.stroke_width if stroke_fill else 0,
        )

    return np.array(img)
