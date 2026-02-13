"""Grayscale / desaturation effect plugin.

Converts the clip to greyscale or partially desaturates it.

Params:
    intensity: float – 0.0 (no change) to 1.0 (full greyscale), default 1.0
"""

from __future__ import annotations

from typing import Any

import numpy as np
from moviepy import VideoClip

from maker8.plugins.base import EffectPlugin, PluginManifest


class GrayscaleEffect(EffectPlugin):
    """Desaturate a clip (partial or full greyscale)."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:grayscale", version="1.0.0")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "intensity": {
                    "type": "number",
                    "default": 1.0,
                    "minimum": 0,
                    "maximum": 1,
                    "description": "0 = original, 1 = full greyscale",
                },
            },
        }

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        intensity = float(params.get("intensity", 1.0))

        if intensity <= 0:
            return ir

        source_clip: VideoClip = ir
        duration = source_clip.duration or 1.0

        # ITU-R BT.601 luma weights
        weights = np.array([0.2989, 0.5870, 0.1140], dtype=np.float32)

        def _make_frame(t: float) -> np.ndarray[Any, Any]:  # type: ignore[type-arg]
            frame = source_clip.get_frame(t).astype(np.float32)
            gray = np.dot(frame[..., :3], weights)
            gray_rgb = np.stack([gray, gray, gray], axis=-1)

            if intensity >= 1.0:
                return gray_rgb.astype(np.uint8)

            blended = frame * (1.0 - intensity) + gray_rgb * intensity
            return np.clip(blended, 0, 255).astype(np.uint8)

        result = VideoClip(_make_frame, duration=duration)
        result = result.with_fps(source_clip.fps or 30)

        if source_clip.audio is not None:
            result = result.with_audio(source_clip.audio)

        return result
