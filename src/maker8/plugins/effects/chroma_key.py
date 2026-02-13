"""Chromatic-key (chroma key / green-screen) effect plugin.

Makes pixels matching a target colour (±tolerance) transparent by setting
their alpha to 0, or replaces them with black.

Since MoviePy compositing handles alpha, this effect zeroes out the
matching pixels, making the underneath layer visible.

Params:
    key_color:  str   – hex colour to key out (default ``"#00FF00"`` green)
    tolerance:  float – Euclidean distance threshold in RGB space (default 80)
    softness:   float – edge softness 0–1, blends near-threshold pixels (default 0.1)
"""

from __future__ import annotations

from typing import Any

import numpy as np
from moviepy import VideoClip

from maker8.plugins.base import EffectPlugin, PluginManifest
from maker8.utils.color import hex_to_rgb


class ChromaKeyEffect(EffectPlugin):
    """Remove a key colour from the clip (green-screen removal)."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:chroma_key", version="1.0.0")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key_color": {"type": "string", "default": "#00FF00"},
                "tolerance": {"type": "number", "default": 80, "minimum": 0},
                "softness": {"type": "number", "default": 0.1, "minimum": 0, "maximum": 1},
            },
        }

    def apply(self, ctx: Any, ir: Any, instance: dict) -> Any:
        params = instance.get("params", {})
        key_color = np.array(
            hex_to_rgb(str(params.get("key_color", "#00FF00"))),
            dtype=np.float32,
        )
        tolerance = float(params.get("tolerance", 80))
        softness = float(params.get("softness", 0.1))

        source_clip: VideoClip = ir
        duration = source_clip.duration or 1.0

        soft_range = max(tolerance * softness, 1.0)

        def _make_frame(t: float) -> np.ndarray[Any, Any]:  # type: ignore[type-arg]
            frame = source_clip.get_frame(t).astype(np.float32)

            # Euclidean distance from key colour per pixel
            diff = np.sqrt(np.sum((frame[..., :3] - key_color) ** 2, axis=-1))

            # Alpha: 0 where matching, 1 where not, smooth in between
            alpha = np.clip((diff - tolerance) / soft_range, 0, 1)

            # Multiply RGB by alpha to simulate transparency over black bg
            result = frame.copy()
            result[..., 0] *= alpha
            result[..., 1] *= alpha
            result[..., 2] *= alpha

            return np.clip(result, 0, 255).astype(np.uint8)  # type: ignore[no-any-return]

        result = VideoClip(_make_frame, duration=duration)
        result = result.with_fps(source_clip.fps or 30)

        if source_clip.audio is not None:
            result = result.with_audio(source_clip.audio)

        return result
