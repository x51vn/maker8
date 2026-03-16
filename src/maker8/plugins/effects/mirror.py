"""Mirror / flip effect plugin.

Flips the clip horizontally, vertically, or both.
Uses MoviePy native ``MirrorX`` / ``MirrorY`` effects instead of
per-frame Python callbacks.

Params:
    horizontal: bool – flip left-right (default true)
    vertical:   bool – flip top-bottom (default false)
"""

from __future__ import annotations

from typing import Any

from moviepy.video.fx import MirrorX, MirrorY

from maker8.plugins.base import EffectPlugin, PluginManifest


class MirrorEffect(EffectPlugin):
    """Flip a clip horizontally and/or vertically."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:mirror", version="1.0.0")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "horizontal": {"type": "boolean", "default": True},
                "vertical": {"type": "boolean", "default": False},
            },
        }

    def has_ffmpeg_filter(self) -> bool:
        return True

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        horizontal = bool(params.get("horizontal", True))
        vertical = bool(params.get("vertical", False))

        if not horizontal and not vertical:
            return ir

        effects: list[MirrorX | MirrorY] = []
        if horizontal:
            effects.append(MirrorX())
        if vertical:
            effects.append(MirrorY())

        return ir.with_effects(effects)
