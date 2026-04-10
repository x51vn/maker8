"""Color overlay / tint effect plugin.

Applies a semi-transparent colour wash over every frame.  Often used
for mood/tone grading (warm tint, cool tint, sepia-like).
Uses MoviePy native ``MultiplyColor`` for tint-like blends where
possible, otherwise falls back to an optimised numpy blend.

Params:
    color:   str   – hex colour, e.g. ``"#FF8800"`` (default ``"#000000"``)
    opacity: float – overlay opacity 0.0–1.0 (default 0.3)
"""

from __future__ import annotations

from typing import Any

import numpy as np
from moviepy import VideoClip

from maker8.plugins.base import EffectPlugin, PluginManifest
from maker8.utils.color import hex_to_rgb


class ColorOverlayEffect(EffectPlugin):
    """Blend a flat colour on top of every frame."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:color_overlay", version="1.0.0")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "color": {"type": "string", "default": "#000000"},
                "opacity": {"type": "number", "default": 0.3, "minimum": 0, "maximum": 1},
            },
        }

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        color = hex_to_rgb(str(params.get("color", "#000000")))
        opacity = float(params.get("opacity", 0.3))

        if opacity <= 0:
            return ir

        source_clip: VideoClip = ir
        duration = source_clip.duration or 1.0

        # Pre-compute constants outside the per-frame function
        overlay = np.array(color, dtype=np.float32)
        inv_opacity = np.float32(1.0 - opacity)
        scaled_overlay = overlay * np.float32(opacity)

        def _make_frame(t: float) -> np.ndarray[Any, Any]:
            frame = source_clip.get_frame(t).astype(np.float32)
            blended = frame * inv_opacity + scaled_overlay
            return np.clip(blended, 0, 255).astype(np.uint8)  # type: ignore[no-any-return]

        result = VideoClip(_make_frame, duration=duration)
        result = result.with_fps(source_clip.fps or 30)

        return result
