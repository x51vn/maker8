"""Built-in effect plugins for the Maker8 render pipeline.

Each module exposes an ``EffectPlugin`` subclass registered by
``PluginRegistry.load_defaults()``.

Available effects (10 total):
    - ``fade``                 – FadeIn / FadeOut opacity transitions
    - ``zoom_pan``             – Ken-Burns zoom & pan
    - ``blur``                 – Gaussian blur (static or animated)
    - ``brightness_contrast``  – brightness / contrast adjustment
    - ``slide``                – slide-in / slide-out from an edge
    - ``color_overlay``        – semi-transparent colour tint
    - ``grayscale``            – full or partial desaturation
    - ``rotate``               – animated or static rotation
    - ``mirror``               – horizontal / vertical flip
    - ``chroma_key``           – green-screen colour removal
"""

from __future__ import annotations
