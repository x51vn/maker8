"""Fade effect plugin – cross-fade opacity at scene start / end.

Params:
    fade_in_duration:  float  – seconds of fade-in  (default 0.5, 0 to disable)
    fade_out_duration: float  – seconds of fade-out (default 0.5, 0 to disable)
"""

from __future__ import annotations

from typing import Any

from moviepy.video.fx import FadeIn, FadeOut

from maker8.plugins.base import EffectPlugin, PluginManifest


class FadeEffect(EffectPlugin):
    """Apply opacity fade-in and/or fade-out to a clip."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:fade", version="1.0.0")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "fade_in_duration": {"type": "number", "default": 0.5, "minimum": 0},
                "fade_out_duration": {"type": "number", "default": 0.5, "minimum": 0},
            },
        }

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        fade_in = float(params.get("fade_in_duration", 0.5))
        fade_out = float(params.get("fade_out_duration", 0.5))

        clip = ir
        effects = []
        if fade_in > 0:
            effects.append(FadeIn(fade_in))
        if fade_out > 0:
            effects.append(FadeOut(fade_out))

        if effects:
            clip = clip.with_effects(effects)
        return clip
