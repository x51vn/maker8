"""Chromatic-key (chroma key / green-screen) effect plugin.

Uses MoviePy native ``MaskColor`` instead of per-frame numpy processing.
Makes pixels matching a target colour (±tolerance) transparent.

Params:
    key_color:  str   – hex colour to key out (default ``"#00FF00"`` green)
    tolerance:  float – colour distance threshold (default 80)
    softness:   float – edge softness 0–1 (default 0.1)
"""

from __future__ import annotations

from typing import Any

from moviepy.video.fx import MaskColor

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

    def has_ffmpeg_filter(self) -> bool:
        return True

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        key_color = hex_to_rgb(str(params.get("key_color", "#00FF00")))
        tolerance = float(params.get("tolerance", 80))
        softness = float(params.get("softness", 0.1))

        # MaskColor's stiffness controls edge hardness (inverse of softness)
        stiffness = max(1.0, 1.0 / softness) if softness > 0 else 100.0

        return ir.with_effects(
            [
                MaskColor(color=key_color, threshold=tolerance, stiffness=stiffness),
            ]
        )
