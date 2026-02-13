"""Zoom-Pan (Ken Burns) effect plugin.

Smoothly zooms from ``start_zoom`` to ``end_zoom`` over the clip's duration,
optionally panning the centre of focus.

Params:
    start_zoom: float – initial scale factor (default 1.0)
    end_zoom:   float – final scale factor  (default 1.2)
    center_x:   float – focus X as fraction 0–1 (default 0.5)
    center_y:   float – focus Y as fraction 0–1 (default 0.5)
"""

from __future__ import annotations

from typing import Any

import numpy as np
from moviepy import ImageClip, VideoClip

from maker8.plugins.base import EffectPlugin, PluginManifest


class ZoomPanEffect(EffectPlugin):
    """Ken-Burns style zoom and pan."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:zoom_pan", version="1.0.0")

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "start_zoom": {"type": "number", "default": 1.0, "minimum": 0.1},
                "end_zoom": {"type": "number", "default": 1.2, "minimum": 0.1},
                "center_x": {"type": "number", "default": 0.5, "minimum": 0, "maximum": 1},
                "center_y": {"type": "number", "default": 0.5, "minimum": 0, "maximum": 1},
            },
        }

    def apply(self, ctx: Any, ir: Any, instance: dict) -> Any:
        params = instance.get("params", {})
        start_zoom = float(params.get("start_zoom", 1.0))
        end_zoom = float(params.get("end_zoom", 1.2))
        center_x = float(params.get("center_x", 0.5))
        center_y = float(params.get("center_y", 0.5))

        source_clip: VideoClip = ir
        w, h = source_clip.size
        duration = source_clip.duration or 1.0

        def _make_frame(t: float) -> np.ndarray:
            progress = t / duration if duration > 0 else 0.0
            zoom = start_zoom + (end_zoom - start_zoom) * progress

            # Crop region in original coordinates
            crop_w = int(w / zoom)
            crop_h = int(h / zoom)

            # Clamp crop to source bounds
            crop_w = min(crop_w, w)
            crop_h = min(crop_h, h)

            # Focus centre
            cx = int(center_x * w)
            cy = int(center_y * h)

            x1 = max(0, min(cx - crop_w // 2, w - crop_w))
            y1 = max(0, min(cy - crop_h // 2, h - crop_h))

            frame = source_clip.get_frame(t)
            cropped = frame[y1 : y1 + crop_h, x1 : x1 + crop_w]

            # Scale back to original size via simple nearest-neighbour resize
            from PIL import Image

            img = Image.fromarray(cropped)
            img = img.resize((w, h), Image.LANCZOS)
            return np.array(img)

        result = VideoClip(_make_frame, duration=duration)
        result = result.with_fps(source_clip.fps or 30)

        # Preserve audio
        if source_clip.audio is not None:
            result = result.with_audio(source_clip.audio)

        return result
