"""Colour parsing utilities used by the rendering engine."""

from __future__ import annotations


def hex_to_rgba(hex_color: str) -> tuple[int, int, int, int]:
    """``"#RRGGBB"`` or ``"#RRGGBBAA"`` → ``(R, G, B, A)``."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    if len(h) == 8:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
    return (255, 255, 255, 255)


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """``"#RRGGBB"`` → ``(R, G, B)``."""
    r, g, b, _ = hex_to_rgba(hex_color)
    return (r, g, b)
