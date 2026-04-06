"""Render styled text to RGBA numpy arrays using Pillow.

This module is the only place in the project that deals with font loading,
text wrapping, stroke drawing, and alignment.  The ``layers`` module calls
``render_text_image()`` and wraps the result in a MoviePy ``ImageClip``.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from maker8.models.spec import TextStyle
from maker8.utils.color import hex_to_rgba
from maker8.utils.logging import get_logger

log = get_logger(__name__)

# ── Font registry ────────────────────────────────────────────────────────────

# Resolve the package-bundled Roboto variable font once at import time.
_fonts_pkg = importlib.resources.files("maker8.assets.fonts.google.roboto")
_ROBOTO_PATH = str(_fonts_pkg.joinpath("Roboto.ttf"))

# Variation-axis tuples: (weight, width).
_BUILTIN_FONTS: dict[str, tuple[str, list[float]]] = {
    "font:roboto:regular": (_ROBOTO_PATH, [400, 100]),
    "font:roboto:bold": (_ROBOTO_PATH, [700, 100]),
}

# Backward-compatible aliases – old font:inter:* refs map to Roboto.
_FONT_ALIASES: dict[str, str] = {
    "font:inter:regular": "font:roboto:regular",
    "font:inter:bold": "font:roboto:bold",
}

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(ref: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return a Pillow font for the given ``font_ref`` and pixel *size*."""
    key = (ref, size)
    if key in _font_cache:
        return _font_cache[key]

    # Resolve legacy aliases.
    canonical = _FONT_ALIASES.get(ref, ref)
    if canonical != ref:
        log.debug("font.alias.resolved", original=ref, canonical=canonical)

    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    builtin = _BUILTIN_FONTS.get(canonical)
    if builtin:
        path, axes = builtin
        font = ImageFont.truetype(path, size)
        font.set_variation_by_axes(axes)
    elif Path(canonical).exists():
        # Direct filesystem path.
        font = ImageFont.truetype(canonical, size)
    else:
        log.warning(
            "font.fallback",
            font_ref=ref,
            reason="unresolved font_ref, using Pillow default",
        )
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
    lines = _wrap_text(text, font, width, draw) if style.wrap else [text]

    line_h = style.size * style.line_height
    total_h = line_h * len(lines)

    # Normalise legacy "middle" → "center".
    if valign == "middle":
        valign = "center"

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
