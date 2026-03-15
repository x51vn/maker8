"""Canonical RenderSpec models – the single source of truth.

Both editor8 and maker8 import from here. Wire format matches
``video.render.request.v1``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Canvas ───────────────────────────────────────────────────────────────────


class SafeArea(BaseModel):
    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0


class Canvas(BaseModel):
    w: int = 1080
    h: int = 1920
    fps: int = 30
    bg: str = "#000000"
    safe_area: SafeArea | None = None


# ── Defaults ─────────────────────────────────────────────────────────────────


class NarrationDefaults(BaseModel):
    lang: str = "vi-VN"
    tts_preset_ref: str = "tts:vi:default"


class SceneTiming(BaseModel):
    head_pad_sec: float = 0.15
    tail_pad_sec: float = 0.45
    duration_mode: str = "auto_from_tts"


class Defaults(BaseModel):
    narration: NarrationDefaults = Field(default_factory=NarrationDefaults)
    scene_timing: SceneTiming = Field(default_factory=SceneTiming)


# ── Assets ───────────────────────────────────────────────────────────────────


class AssetSourceOptions(BaseModel):
    # Wire-format boundary semantics:
    #   None / omitted → consumer uses its default (e.g. maker8 _DEFAULT_FORMAT)
    #   empty string   → invalid, rejected by consumer
    #   non-empty str  → used as-is
    format: str | None = None
    max_duration_sec: int | None = None


class AssetSource(BaseModel):
    kind: str  # "youtube" | "http"
    url: str
    options: AssetSourceOptions = Field(default_factory=AssetSourceOptions)


class Asset(BaseModel):
    id: str
    type: str  # "video" | "image" | "audio"
    source: AssetSource


# ── Layers ───────────────────────────────────────────────────────────────────


class Rect(BaseModel):
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


class Trim(BaseModel):
    in_: float = Field(0, alias="in")
    out: float = 0

    model_config = {"populate_by_name": True}


class TextStyle(BaseModel):
    font_ref: str = "font:inter:regular"
    size: int = 48
    color: str = "#FFFFFF"
    stroke_color: str | None = None
    stroke_width: int = 0
    line_height: float = 1.15
    wrap: bool = True


class Layer(BaseModel):
    layer_id: str
    type: Literal["image", "video", "text"]
    rect: Rect = Field(default_factory=Rect)
    anchor: str = "top_left"
    opacity: float = 1.0
    rotation_deg: float = 0.0
    scale: float = 1.0

    # image / video
    asset_ref: str | None = None
    fit: str | None = None
    align: str | None = None
    trim: Trim | None = None

    # text
    text: str | None = None
    text_align: str | None = None
    valign: str | None = None
    style: TextStyle | None = None


# ── Audio ────────────────────────────────────────────────────────────────────


class AudioTrack(BaseModel):
    asset_ref: str
    trim: Trim | None = None
    volume: float = 1.0
    loop: bool = False


# ── Effects ──────────────────────────────────────────────────────────────────


class EffectInstance(BaseModel):
    plugin_id: str
    params: dict[str, Any] = Field(default_factory=dict)


# ── Transition ───────────────────────────────────────────────────────────────


class Transition(BaseModel):
    type: str = "crossfade"
    duration: float = 0.5


# ── Scene ────────────────────────────────────────────────────────────────────


class SceneNarration(BaseModel):
    text: str
    lang: str | None = None
    tts_preset_ref: str | None = None


class Scene(BaseModel):
    scene_id: str
    duration: float | None = None
    narration: SceneNarration
    layers: list[Layer] = Field(default_factory=list)
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
    effects: list[EffectInstance] = Field(default_factory=list)
    transition_out: Transition | None = None


# ── Output ───────────────────────────────────────────────────────────────────


class OutputConfig(BaseModel):
    codec: str = "auto"
    audio_codec: str = "aac"
    bitrate: str = "4000k"
    audio_bitrate: str = "192k"
    preset: str = "medium"
    pix_fmt: str = "yuv420p"


# ── Publish ──────────────────────────────────────────────────────────────────


class PublishTarget(BaseModel):
    platform: str
    account_ref: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class PublishConfig(BaseModel):
    targets: list[PublishTarget] = Field(default_factory=list)


# ── Root specs ───────────────────────────────────────────────────────────────


class RenderSpec(BaseModel):
    spec_version: str = "1.0"
    canvas: Canvas = Field(default_factory=Canvas)
    defaults: Defaults = Field(default_factory=Defaults)
    assets: list[Asset] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    output: OutputConfig = Field(default_factory=OutputConfig)
    publish: PublishConfig = Field(default_factory=PublishConfig)


class Trace(BaseModel):
    correlation_id: str = ""


class ResultDestination(BaseModel):
    type: str = "kafka"
    topic: str = "video.render.result.v1"
    key: str = ""


class RenderRequest(BaseModel):
    """``video.render.request.v1`` – the output payload published to Kafka."""

    job_id: str
    spec_version: str = "1.0"
    render_spec: RenderSpec
    result: ResultDestination = Field(default_factory=ResultDestination)
    trace: Trace = Field(default_factory=Trace)
