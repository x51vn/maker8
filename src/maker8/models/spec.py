"""RenderSpec – re-exported from the canonical ``render_contracts`` package.

All wire-format models used by both editor8 and maker8 are defined once
in ``render_contracts.render_spec``.  This module re-exports them so that
existing ``from maker8.models.spec import X`` statements continue to work.
"""

from __future__ import annotations

from render_contracts.render_spec import (  # noqa: F401
    Asset,
    AssetSource,
    AssetSourceOptions,
    AudioTrack,
    Canvas,
    Defaults,
    EffectInstance,
    Layer,
    NarrationDefaults,
    OutputConfig,
    PlanningMetadata,
    PublishConfig,
    PublishTarget,
    Rect,
    RenderSpec,
    ResultDestination,
    SafeArea,
    Scene,
    SceneBoundary,
    SceneNarration,
    SceneSubtitle,
    SceneTiming,
    SourceAttribution,
    SubtitleDefaults,
    TextStyle,
    Trace,
    Transition,
    Trim,
    UploaderMetadata,
)

__all__ = [
    "Asset",
    "AssetSource",
    "AssetSourceOptions",
    "AudioTrack",
    "Canvas",
    "Defaults",
    "EffectInstance",
    "Layer",
    "NarrationDefaults",
    "OutputConfig",
    "PlanningMetadata",
    "PublishConfig",
    "PublishTarget",
    "Rect",
    "RenderSpec",
    "ResultDestination",
    "SafeArea",
    "Scene",
    "SceneBoundary",
    "SceneNarration",
    "SceneSubtitle",
    "SceneTiming",
    "SourceAttribution",
    "SubtitleDefaults",
    "TextStyle",
    "Trace",
    "Transition",
    "Trim",
    "UploaderMetadata",
]
