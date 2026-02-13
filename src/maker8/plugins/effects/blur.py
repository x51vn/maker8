"""Gaussian blur effect plugin.

Applies a Gaussian blur to every frame.  Can be static or animated
(blur amount interpolated over time).

Params:
    radius:       float – blur radius in pixels (default 5.0)
    end_radius:   float | None – if set, interpolates from *radius* to *end_radius*
"""

from __future__ import annotations

from typing import Any

import numpy as np
from moviepy import VideoClip
from PIL import Image, ImageFilter

from maker8.plugins.base import EffectPlugin, PluginManifest


class BlurEffect(EffectPlugin):
    """Apply Gaussian blur (static or animated) to a clip."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:blur", version="1.0.0")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "radius": {"type": "number", "default": 5.0, "minimum": 0},
                "end_radius": {"type": ["number", "null"], "default": None, "minimum": 0},
            },
        }

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        start_radius = float(params.get("radius", 5.0))
        end_radius_raw = params.get("end_radius")
        end_radius = float(end_radius_raw) if end_radius_raw is not None else None

        source_clip: VideoClip = ir
        duration = source_clip.duration or 1.0

        def _make_frame(t: float) -> np.ndarray[Any, Any]:
            frame = source_clip.get_frame(t)

            if end_radius is not None:
                progress = t / duration if duration > 0 else 0.0
                r = start_radius + (end_radius - start_radius) * progress
            else:
                r = start_radius

            if r <= 0:
                return frame  # type: ignore[no-any-return]

            img = Image.fromarray(frame)
            img = img.filter(ImageFilter.GaussianBlur(radius=r))
            return np.array(img)

        result = VideoClip(_make_frame, duration=duration)
        result = result.with_fps(source_clip.fps or 30)

        if source_clip.audio is not None:
            result = result.with_audio(source_clip.audio)

        return result
