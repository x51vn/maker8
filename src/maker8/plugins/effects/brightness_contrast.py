"""Brightness / Contrast adjustment effect plugin.

Changes the brightness and contrast of every frame using Pillow's
``ImageEnhance`` (multiplicative factor, where 1.0 = no change).

Params:
    brightness: float – multiplicative factor (0.0 … 3.0, default 1.0;
                       >1 brighter, <1 darker)
    contrast:   float – multiplicative factor (0.0 … 3.0, default 1.0;
                       >1 more contrast, <1 less)
"""

from __future__ import annotations

from typing import Any

import numpy as np
from moviepy import VideoClip
from PIL import Image, ImageEnhance

from maker8.plugins.base import EffectPlugin, PluginManifest


class BrightnessContrastEffect(EffectPlugin):
    """Adjust brightness and contrast of a clip."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="effect:brightness_contrast", version="1.0.0")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "brightness": {
                    "type": "number",
                    "default": 1.0,
                    "description": "Brightness factor (1.0 = no change, >1 brighter, <1 darker)",
                },
                "contrast": {
                    "type": "number",
                    "default": 1.0,
                    "description": "Contrast factor (1.0 = no change, >1 more contrast)",
                },
            },
        }

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        brightness = float(params.get("brightness", 1.0))
        contrast = float(params.get("contrast", 1.0))

        # Skip if no change
        if brightness == 1.0 and contrast == 1.0:
            return ir

        source_clip: VideoClip = ir
        duration = source_clip.duration or 1.0

        def _make_frame(t: float) -> np.ndarray[Any, Any]:
            frame = source_clip.get_frame(t)
            img = Image.fromarray(frame)

            if brightness != 1.0:
                img = ImageEnhance.Brightness(img).enhance(brightness)
            if contrast != 1.0:
                img = ImageEnhance.Contrast(img).enhance(contrast)

            return np.array(img)

        result = VideoClip(_make_frame, duration=duration)
        result = result.with_fps(source_clip.fps or 30)

        if source_clip.audio is not None:
            result = result.with_audio(source_clip.audio)

        return result
