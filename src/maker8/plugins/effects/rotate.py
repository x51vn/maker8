"""Rotate effect plugin.

Smoothly rotates the clip from ``start_angle`` to ``end_angle`` over its
duration.  Static rotation is achieved by setting both to the same value.

Params:
    start_angle: float – degrees at t=0 (default 0)
    end_angle:   float – degrees at t=end (default 360)
    expand:      bool  – if true, canvas expands to avoid crop (default false)
"""

from __future__ import annotations

from typing import Any

import numpy as np
from moviepy import VideoClip
from PIL import Image

from maker8.plugins.base import EffectPlugin, PluginManifest


class RotateEffect(EffectPlugin):
    """Animated or static rotation effect."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:rotate", version="1.0.0")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "start_angle": {"type": "number", "default": 0},
                "end_angle": {"type": "number", "default": 360},
                "expand": {"type": "boolean", "default": False},
            },
        }

    def apply(self, ctx: Any, ir: Any, instance: dict) -> Any:
        params = instance.get("params", {})
        start_angle = float(params.get("start_angle", 0))
        end_angle = float(params.get("end_angle", 360))
        expand = bool(params.get("expand", False))

        source_clip: VideoClip = ir
        w, h = source_clip.size
        duration = source_clip.duration or 1.0

        def _make_frame(t: float) -> np.ndarray:
            progress = t / duration if duration > 0 else 0.0
            angle = start_angle + (end_angle - start_angle) * progress

            frame = source_clip.get_frame(t)
            img = Image.fromarray(frame)
            rotated = img.rotate(
                -angle,  # PIL positive = counter-clockwise; negate for intuitive CW
                resample=Image.Resampling.BICUBIC,
                expand=expand,
                fillcolor=(0, 0, 0),
            )

            if not expand:
                return np.array(rotated)

            # Resize back to original dimensions when expand=True
            rotated = rotated.resize((w, h), Image.Resampling.LANCZOS)
            return np.array(rotated)

        result = VideoClip(_make_frame, duration=duration)
        result = result.with_fps(source_clip.fps or 30)

        if source_clip.audio is not None:
            result = result.with_audio(source_clip.audio)

        return result
