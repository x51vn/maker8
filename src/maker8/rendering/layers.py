"""Convert scene layer definitions into MoviePy 2.x clips.

Every public function here returns a ``VideoClip`` (or ``None``) that is
ready to be composited.  This module must NOT import from ``pipeline/``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from moviepy import ImageClip, VideoFileClip
from PIL import Image

from maker8.models.spec import Canvas, Layer, TextStyle, Trim
from maker8.rendering.text import render_text_image
from maker8.utils.logging import get_logger

log = get_logger(__name__)

# ── Public entry point ───────────────────────────────────────────────────────


def build_layer_clip(
    layer: Layer,
    asset_paths: dict[str, Path],
    duration: float,
    canvas: Canvas,
    effective_trim: Trim | None = None,
) -> VideoFileClip | ImageClip | None:
    """Dispatch on ``layer.type`` and return a positioned MoviePy clip."""
    if layer.type == "video":
        return _build_video(layer, asset_paths, duration, effective_trim=effective_trim)
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
    effective_trim: Trim | None = None,
) -> VideoFileClip | None:
    if not layer.asset_ref or layer.asset_ref not in asset_paths:
        return None

    clip = VideoFileClip(str(asset_paths[layer.asset_ref])).without_audio()

    # Trim — effective_trim (from scene_clip_select) takes precedence over layer.trim
    trim = effective_trim if effective_trim is not None else layer.trim
    if trim:
        t_start = trim.in_
        t_end = trim.out if trim.out > 0 else clip.duration
        clip = clip.subclipped(t_start, min(t_end, clip.duration))

    # Fit / resize
    clip = _apply_fit(clip, layer)

    # Clamp to scene duration
    if clip.duration > duration:
        clip = clip.subclipped(0, duration)

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

    asset_path = asset_paths[layer.asset_ref]
    try:
        clip = ImageClip(str(asset_path))
    except ImportError as exc:
        # Some imageio plugin paths try optional ITK/SimpleITK backends.
        # Fall back to Pillow decoding so rendering can continue.
        message = str(exc).lower()
        if "itk" not in message and "simpleitk" not in message:
            raise
        log.warning(
            "layers.image_loader_fallback",
            asset_ref=layer.asset_ref,
            path=str(asset_path),
            reason=str(exc),
        )
        with Image.open(asset_path) as image:
            clip = ImageClip(np.array(image.convert("RGBA")), is_mask=False)
    clip = _apply_fit(clip, layer)
    clip = clip.with_duration(duration)
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

    clip = ImageClip(arr, is_mask=False).with_duration(duration)
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
        clip = clip.resized(height=rh) if clip_ratio > target_ratio else clip.resized(width=rw)
        # Centre-crop to rect
        cw2, ch2 = clip.size
        x1 = (cw2 - rw) // 2
        y1 = (ch2 - rh) // 2
        clip = clip.cropped(x1=x1, y1=y1, x2=x1 + rw, y2=y1 + rh)
    else:  # contain
        clip = clip.resized(width=rw) if clip_ratio > target_ratio else clip.resized(height=rh)

    return clip


# ── Geometry (position, opacity, anchor, rotation, scale) ────────────────────


def _apply_geometry(clip: object, layer: Layer) -> object:
    """Set position, opacity, rotation, and scale on a MoviePy 2.x clip."""
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

    clip = clip.with_position((x, y))  # type: ignore[attr-defined]

    if layer.opacity < 1.0:
        clip = clip.with_opacity(layer.opacity)  # type: ignore[attr-defined]

    if layer.rotation_deg != 0.0:
        clip = clip.rotated(layer.rotation_deg)  # type: ignore[attr-defined]

    if layer.scale != 1.0:
        new_w = int(clip.size[0] * layer.scale)  # type: ignore[attr-defined]
        new_h = int(clip.size[1] * layer.scale)  # type: ignore[attr-defined]
        clip = clip.resized(width=new_w, height=new_h)  # type: ignore[attr-defined]

    return clip
