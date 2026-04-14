"""Shared enums and value-objects used across models, pipeline, and services.

>>> SINGLE SOURCE OF TRUTH – never duplicate these types elsewhere. <<<

Wire-format types (``PublishTarget``, ``Trace``) are re-exported from the
canonical ``render_contracts`` package so that existing imports from
``maker8.models.common`` continue to work.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from render_contracts.render_spec import PublishTarget, Trace  # noqa: F401

__all__ = [
    "AssetWarning",
    "DropboxFileRef",
    "EngineVersions",
    "ErrorInfo",
    "JobStatus",
    "OutputMeta",
    "PerformanceMode",
    "PublishStage",
    "PublishStatus",
    "PublishTarget",
    "RenderStage",
    "Trace",
]

# ── Enums ────────────────────────────────────────────────────────────────────


class RenderStage(StrEnum):
    """Pipeline stages for the Render Worker."""

    VALIDATE = "VALIDATE"
    RESOLVE_ASSETS = "RESOLVE_ASSETS"
    DOWNLOAD = "DOWNLOAD"
    SCENE_DETECT = "SCENE_DETECT"
    NORMALIZE = "NORMALIZE"
    TTS = "TTS"
    RENDER = "RENDER"
    UPLOAD_DROPBOX = "UPLOAD_DROPBOX"
    EMIT_RESULT = "EMIT_RESULT"


class PublishStage(StrEnum):  # RESERVED – publisher worker not yet implemented
    """Pipeline stages for the Publisher Worker (not yet implemented)."""

    DOWNLOAD = "DOWNLOAD"
    PUBLISH = "PUBLISH"
    EMIT_RESULT = "EMIT_RESULT"


class JobStatus(StrEnum):
    DONE = "DONE"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class PublishStatus(StrEnum):  # RESERVED – publisher worker not yet implemented
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    PENDING = "PENDING"


class PerformanceMode(StrEnum):
    """Render quality/speed trade-off."""

    QUALITY = "quality"
    BALANCED = "balanced"
    FAST = "fast"


# ── Shared value-objects ─────────────────────────────────────────────────────


class ErrorInfo(BaseModel):
    """Serialisable error data included in Kafka result / DLQ messages."""

    code: str
    stage: str
    retryable: bool = False
    message: str = ""


class DropboxFileRef(BaseModel):
    """Reference to a file stored in Dropbox."""

    path: str = ""
    file_id: str = ""
    rev: str = ""
    content_hash: str = ""
    sha256: str = ""
    bytes_: int = Field(0, alias="bytes")
    mime: str = ""

    model_config = {"populate_by_name": True}


class OutputMeta(BaseModel):
    """Basic metadata about the rendered video."""

    duration: float = 0
    w: int = 0
    h: int = 0
    fps: float = 0
    size_bytes: int = 0


class EngineVersions(BaseModel):
    """Versions of the core tools used during rendering."""

    moviepy: str = ""
    ffmpeg: str = ""
    youtube_dlp: str = ""


# ── Degradation tracking ─────────────────────────────────────────────────────


class AssetWarning(BaseModel):
    """Describes one asset/scene-level issue that was tolerated during rendering.

    Collected in ``PipelineContext.warnings`` and surfaced in ``RenderResult``.
    """

    asset_id: str = ""
    scene_id: str = ""
    stage: str = ""
    code: str = ""
    message: str = ""
    fallback_used: str = ""
