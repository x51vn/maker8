"""Kafka message contracts: request, result, and DLQ payloads.

Wire-format types (``RenderRequest``, ``ResultDestination``) are imported
from the canonical ``render_contracts`` package.  maker8-specific result
and DLQ types are defined here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from maker8.models.common import (
    AssetWarning,
    DropboxFileRef,
    EngineVersions,
    ErrorInfo,
    JobStatus,
    OutputMeta,
    PublishTarget,
    Trace,
)
from render_contracts.render_spec import (  # noqa: F401
    RenderRequest,
    ResultDestination,
    UploaderMetadata,
)

__all__ = [
    "DLQPayload",
    "DropboxOutput",
    "RenderRequest",
    "RenderResult",
    "ResultDestination",
    "UploaderMetadata",
]

# ── Render Result ────────────────────────────────────────────────────────────


class DropboxOutput(BaseModel):
    """References to the files uploaded to Dropbox."""

    video: DropboxFileRef = Field(default_factory=DropboxFileRef)
    manifest: DropboxFileRef = Field(default_factory=DropboxFileRef)


class RenderResult(BaseModel):
    """``video.render.result.v1`` payload."""

    job_id: str
    status: JobStatus
    job_key: str = ""
    dry_run: bool = False
    canvas_profile: str | None = None
    dropbox: DropboxOutput = Field(default_factory=DropboxOutput)
    output_meta: OutputMeta = Field(default_factory=OutputMeta)
    uploader_metadata: UploaderMetadata = Field(default_factory=UploaderMetadata)
    publish_targets: list[PublishTarget] = Field(default_factory=list)
    asset_report: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[AssetWarning] = Field(default_factory=list)
    engine_versions: EngineVersions = Field(default_factory=EngineVersions)
    trace: Trace = Field(default_factory=Trace)
    error: ErrorInfo | None = None


# ── DLQ ──────────────────────────────────────────────────────────────────────


class DLQPayload(BaseModel):
    """``video.render.dlq.v1`` payload."""

    job_id: str
    job_key: str = ""
    failed_stage: str
    attempts: int
    max_attempts: int = 0
    last_error: ErrorInfo | None = None
    dropbox: dict[str, Any] = Field(default_factory=dict)
    trace: Trace = Field(default_factory=Trace)
    debug_context: dict[str, Any] = Field(default_factory=dict)
