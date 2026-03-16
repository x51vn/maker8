"""Rotate effect plugin.

Smoothly rotates the clip from ``start_angle`` to ``end_angle`` over its
duration.  Uses MoviePy native ``Rotate`` effect with a callable angle
function for animated rotation.  Static rotation uses a constant.

Params:
    start_angle: float – degrees at t=0 (default 0)
    end_angle:   float – degrees at t=end (default 360)
    expand:      bool  – if true, canvas expands to avoid crop (default false)
"""

from __future__ import annotations

from typing import Any

from moviepy.video.fx import Resize, Rotate

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

    def has_ffmpeg_filter(self) -> bool:
        return True

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        start_angle = float(params.get("start_angle", 0))
        end_angle = float(params.get("end_angle", 360))
        expand = bool(params.get("expand", False))

        clip = ir
        w, h = clip.size
        duration = clip.duration or 1.0

        # Static rotation: constant angle
        if start_angle == end_angle:
            clip = clip.with_effects(
                [
                    Rotate(angle=start_angle, expand=expand, bg_color=(0, 0, 0)),
                ]
            )
            if expand:
                clip = clip.with_effects([Resize(new_size=(w, h))])
            return clip

        # Animated rotation: callable angle(t)
        def _angle(t: float) -> float:
            progress = t / duration if duration > 0 else 0.0
            return start_angle + (end_angle - start_angle) * progress

        clip = clip.with_effects(
            [
                Rotate(angle=_angle, expand=expand, bg_color=(0, 0, 0)),
            ]
        )
        if expand:
            clip = clip.with_effects([Resize(new_size=(w, h))])
        return clip
