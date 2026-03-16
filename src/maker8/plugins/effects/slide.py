"""Slide-in / Slide-out effect plugin.

Animates the clip sliding in from (or out to) an edge of the canvas.

Params:
    direction:    str   – "left" | "right" | "top" | "bottom" (default "left")
    slide_in:     bool  – animate entrance (default true)
    slide_out:    bool  – animate exit (default false)
    in_duration:  float – seconds for slide-in  (default 0.5)
    out_duration: float – seconds for slide-out (default 0.5)
"""

from __future__ import annotations

from typing import Any

from moviepy import VideoClip

from maker8.plugins.base import EffectPlugin, PluginManifest


def _ease_out_cubic(t: float) -> float:
    """Cubic ease-out for smooth deceleration."""
    return 1.0 - (1.0 - t) ** 3


def _ease_in_cubic(t: float) -> float:
    """Cubic ease-in for smooth acceleration."""
    return t**3


class SlideEffect(EffectPlugin):
    """Slide a clip in from / out to a canvas edge."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:slide", version="1.0.0")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["left", "right", "top", "bottom"],
                    "default": "left",
                },
                "slide_in": {"type": "boolean", "default": True},
                "slide_out": {"type": "boolean", "default": False},
                "in_duration": {"type": "number", "default": 0.5, "minimum": 0},
                "out_duration": {"type": "number", "default": 0.5, "minimum": 0},
            },
        }

    def has_ffmpeg_filter(self) -> bool:
        return True

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        direction = str(params.get("direction", "left"))
        do_slide_in = bool(params.get("slide_in", True))
        do_slide_out = bool(params.get("slide_out", False))
        in_dur = float(params.get("in_duration", 0.5))
        out_dur = float(params.get("out_duration", 0.5))

        source_clip: VideoClip = ir
        w, h = source_clip.size
        duration = source_clip.duration or 1.0

        # Off-screen start/end deltas
        if direction == "left":
            dx_in, dy_in = -w, 0
        elif direction == "right":
            dx_in, dy_in = w, 0
        elif direction == "top":
            dx_in, dy_in = 0, -h
        else:  # bottom
            dx_in, dy_in = 0, h

        def _position(t: float) -> tuple[float, float]:
            offset_x, offset_y = 0.0, 0.0

            # Slide-in phase
            if do_slide_in and t < in_dur and in_dur > 0:
                progress = _ease_out_cubic(t / in_dur)
                offset_x += dx_in * (1.0 - progress)
                offset_y += dy_in * (1.0 - progress)

            # Slide-out phase
            if do_slide_out and t > (duration - out_dur) and out_dur > 0:
                progress = _ease_in_cubic((t - (duration - out_dur)) / out_dur)
                offset_x += dx_in * progress
                offset_y += dy_in * progress

            return (offset_x, offset_y)

        return source_clip.with_position(_position)
