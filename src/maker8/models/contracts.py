"""Kafka message contracts: request, result, and DLQ payloads."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from maker8.models.common import (
    DropboxFileRef,
    EngineVersions,
    ErrorInfo,
    JobStatus,
    OutputMeta,
    PublishTarget,
    Trace,
)
from maker8.models.spec import RenderSpec


# ── Render Request ───────────────────────────────────────────────────────────


class ResultDestination(BaseModel):
    """Where to deliver the render result."""

    type: str = "kafka"
    topic: str = "video.render.result.v1"
    key: str = ""


class RenderRequest(BaseModel):
    """``video.render.request.v1`` payload."""

    job_id: str
    spec_version: str = "1.0"
    render_spec: RenderSpec
    result: ResultDestination = Field(default_factory=ResultDestination)
    trace: Trace = Field(default_factory=Trace)


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
    dropbox: DropboxOutput = Field(default_factory=DropboxOutput)
    output_meta: OutputMeta = Field(default_factory=OutputMeta)
    publish_targets: list[PublishTarget] = Field(default_factory=list)
    asset_report: list[dict[str, Any]] = Field(default_factory=list)
    engine_versions: EngineVersions = Field(default_factory=EngineVersions)
    error: ErrorInfo | None = None


# ── DLQ ──────────────────────────────────────────────────────────────────────


class DLQPayload(BaseModel):
    """``video.render.dlq.v1`` payload."""

    job_id: str
    job_key: str = ""
    failed_stage: str
    attempts: int
    last_error: ErrorInfo | None = None
    dropbox: dict[str, Any] = Field(default_factory=dict)
    trace: Trace = Field(default_factory=Trace)
