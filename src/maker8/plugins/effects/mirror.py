"""Mirror / flip effect plugin.

Flips the clip horizontally, vertically, or both.

Params:
    horizontal: bool – flip left-right (default true)
    vertical:   bool – flip top-bottom (default false)
"""

from __future__ import annotations

from typing import Any

import numpy as np
from moviepy import VideoClip

from maker8.plugins.base import EffectPlugin, PluginManifest


class MirrorEffect(EffectPlugin):
    """Flip a clip horizontally and/or vertically."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:mirror", version="1.0.0")

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "horizontal": {"type": "boolean", "default": True},
                "vertical": {"type": "boolean", "default": False},
            },
        }

    def apply(self, ctx: Any, ir: Any, instance: dict) -> Any:
        params = instance.get("params", {})
        horizontal = bool(params.get("horizontal", True))
        vertical = bool(params.get("vertical", False))

        if not horizontal and not vertical:
            return ir

        source_clip: VideoClip = ir
        duration = source_clip.duration or 1.0

        def _make_frame(t: float) -> np.ndarray:
            frame = source_clip.get_frame(t)
            if horizontal:
                frame = np.fliplr(frame)
            if vertical:
                frame = np.flipud(frame)
            return frame.copy()  # ensure C-contiguous

        result = VideoClip(_make_frame, duration=duration)
        result = result.with_fps(source_clip.fps or 30)

        if source_clip.audio is not None:
            result = result.with_audio(source_clip.audio)

        return result
