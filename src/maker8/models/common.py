"""Shared enums and value-objects used across models, pipeline, and services.

>>> SINGLE SOURCE OF TRUTH – never duplicate these types elsewhere. <<<

Wire-format types (``PublishTarget``, ``Trace``) are re-exported from the
canonical ``render_contracts`` package so that existing imports from
``maker8.models.common`` continue to work.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from render_contracts.render_spec import PublishTarget, Trace  # noqa: F401

__all__ = [
    "DropboxFileRef",
    "EngineVersions",
    "ErrorInfo",
    "JobStatus",
    "OutputMeta",
    "PublishStage",
    "PublishStatus",
    "PublishTarget",
    "RenderStage",
    "Trace",
]

# ── Enums ────────────────────────────────────────────────────────────────────


class RenderStage(str, Enum):
    """Pipeline stages for the Render Worker."""

    VALIDATE = "VALIDATE"
    RESOLVE_ASSETS = "RESOLVE_ASSETS"
    DOWNLOAD = "DOWNLOAD"
    NORMALIZE = "NORMALIZE"
    TTS = "TTS"
    RENDER = "RENDER"
    UPLOAD_DROPBOX = "UPLOAD_DROPBOX"
    EMIT_RESULT = "EMIT_RESULT"


class PublishStage(str, Enum):
    """Pipeline stages for the Publisher Worker (future)."""

    DOWNLOAD = "DOWNLOAD"
    PUBLISH = "PUBLISH"
    EMIT_RESULT = "EMIT_RESULT"


class JobStatus(str, Enum):
    DONE = "DONE"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class PublishStatus(str, Enum):
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    PENDING = "PENDING"


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


