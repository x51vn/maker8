"""Built-in effect plugins for the Maker8 render pipeline.

Each module exposes an ``EffectPlugin`` subclass registered by
``PluginRegistry.load_defaults()``.

Available effects:
    - ``fade`` – FadeIn / FadeOut opacity transitions
    - ``zoom_pan`` – Ken-Burns zoom & pan
    - ``blur`` – Gaussian blur (static or animated)
    - ``brightness_contrast`` – brightness / contrast adjustment
    - ``slide`` – slide-in / slide-out from an edge
"""

from __future__ import annotations
