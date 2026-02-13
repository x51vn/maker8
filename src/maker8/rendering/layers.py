"""Convert scene layer definitions into MoviePy clips.

Every public function here returns a ``VideoClip`` (or ``None``) that is
ready to be composited.  This module must NOT import from ``pipeline/``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from moviepy.editor import ImageClip, VideoFileClip

from maker8.models.spec import Canvas, Layer, TextStyle
from maker8.rendering.text import render_text_image


# ── Public entry point ───────────────────────────────────────────────────────


def build_layer_clip(
    layer: Layer,
    asset_paths: dict[str, Path],
    duration: float,
    canvas: Canvas,
) -> VideoFileClip | ImageClip | None:
    """Dispatch on ``layer.type`` and return a positioned MoviePy clip."""
    if layer.type == "video":
        return _build_video(layer, asset_paths, duration)
    if layer.type == "image":
        return _build_image(layer, asset_paths, duration)
    if layer.type == "text":
        return _build_text(layer, duration)
    return None


# ── Video layer ──────────────────────────────────────────────────────────────


def _build_video(
    layer: Layer,
    asset_paths: dict[str, Path],
    duration: float,
) -> VideoFileClip | None:
    if not layer.asset_ref or layer.asset_ref not in asset_paths:
        return None

    clip = VideoFileClip(str(asset_paths[layer.asset_ref]))

    # Trim
    if layer.trim:
        t_start = layer.trim.in_
        t_end = layer.trim.out if layer.trim.out > 0 else clip.duration
        clip = clip.subclip(t_start, min(t_end, clip.duration))

    # Fit / resize
    clip = _apply_fit(clip, layer)

    # Clamp to scene duration
    if clip.duration > duration:
        clip = clip.subclip(0, duration)

    clip = _apply_geometry(clip, layer)
    return clip


# ── Image layer ──────────────────────────────────────────────────────────────


def _build_image(
    layer: Layer,
    asset_paths: dict[str, Path],
    duration: float,
) -> ImageClip | None:
    if not layer.asset_ref or layer.asset_ref not in asset_paths:
        return None

    clip = ImageClip(str(asset_paths[layer.asset_ref]))
    clip = _apply_fit(clip, layer)
    clip = clip.set_duration(duration)
    clip = _apply_geometry(clip, layer)
    return clip


# ── Text layer ───────────────────────────────────────────────────────────────


def _build_text(layer: Layer, duration: float) -> ImageClip | None:
    if not layer.text:
        return None

    style = layer.style or TextStyle()
    w = layer.rect.w or 920
    h = layer.rect.h or 360

    arr = render_text_image(
        text=layer.text,
        width=w,
        height=h,
        style=style,
        text_align=layer.text_align or "left",
        valign=layer.valign or "top",
    )

    clip = ImageClip(arr, ismask=False).set_duration(duration)
    clip = _apply_geometry(clip, layer)
    return clip


# ── Fit / resize logic ──────────────────────────────────────────────────────


def _apply_fit(clip: VideoFileClip | ImageClip, layer: Layer) -> VideoFileClip | ImageClip:
    """Resize *clip* to fit ``layer.rect`` according to ``layer.fit``."""
    rw, rh = layer.rect.w, layer.rect.h
    if rw <= 0 or rh <= 0:
        return clip

    cw, ch = clip.size
    target_ratio = rw / rh
    clip_ratio = cw / ch
    fit = layer.fit or "cover"

    if fit == "cover":
        if clip_ratio > target_ratio:
            clip = clip.resize(height=rh)
        else:
            clip = clip.resize(width=rw)
        # Centre-crop to rect
        cw2, ch2 = clip.size
        x1 = (cw2 - rw) // 2
        y1 = (ch2 - rh) // 2
        clip = clip.crop(x1=x1, y1=y1, x2=x1 + rw, y2=y1 + rh)
    else:  # contain
        if clip_ratio > target_ratio:
            clip = clip.resize(width=rw)
        else:
            clip = clip.resize(height=rh)

    return clip


# ── Geometry (position, opacity, anchor) ─────────────────────────────────────


def _apply_geometry(clip: object, layer: Layer) -> object:
    """Set position and opacity on a MoviePy clip."""
    x, y = layer.rect.x, layer.rect.y
    cw, ch = clip.size  # type: ignore[attr-defined]

    anchor = layer.anchor or "top_left"
    if anchor == "center":
        x = layer.rect.x + (layer.rect.w - cw) // 2
        y = layer.rect.y + (layer.rect.h - ch) // 2
    elif anchor == "bottom_left":
        y = layer.rect.y + layer.rect.h - ch
    elif anchor == "bottom_right":
        x = layer.rect.x + layer.rect.w - cw
        y = layer.rect.y + layer.rect.h - ch

    clip = clip.set_position((x, y))  # type: ignore[attr-defined]

    if layer.opacity < 1.0:
        clip = clip.set_opacity(layer.opacity)  # type: ignore[attr-defined]

    return clip
