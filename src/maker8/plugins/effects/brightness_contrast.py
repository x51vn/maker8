"""Brightness / Contrast adjustment effect plugin.

Uses MoviePy native ``LumContrast`` instead of per-frame Pillow
``ImageEnhance``.  The ``lum`` parameter maps to luminosity offset and
``contrast`` maps to the contrast multiplier.

Params:
    brightness: float – multiplicative factor (0.0 … 3.0, default 1.0;
                       >1 brighter, <1 darker)
    contrast:   float – multiplicative factor (0.0 … 3.0, default 1.0;
                       >1 more contrast, <1 less)
"""

from __future__ import annotations

from typing import Any

from moviepy.video.fx import LumContrast

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

    def has_ffmpeg_filter(self) -> bool:
        return True

    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        params = instance.get("params", {})
        brightness = float(params.get("brightness", 1.0))
        contrast = float(params.get("contrast", 1.0))

        # Skip if no change
        if brightness == 1.0 and contrast == 1.0:
            return ir

        # Convert multiplicative brightness (1.0 = no change) to
        # LumContrast's additive lum offset:  lum = (factor - 1) * 128
        # and contrast multiplier (1.0 = no change) to additive:
        # contrast_param = (factor - 1) * 128
        lum = (brightness - 1.0) * 128.0
        contrast_val = (contrast - 1.0) * 128.0

        return ir.with_effects([LumContrast(lum=lum, contrast=contrast_val)])
