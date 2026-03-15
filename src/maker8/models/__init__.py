"""Pydantic v2 models – public re-exports.

Wire-format types originate from ``render_contracts`` and are re-exported
through ``maker8.models.spec`` and ``maker8.models.common`` for backward
compatibility with existing import paths.
"""

from __future__ import annotations

from maker8.models.common import (
    DropboxFileRef,
    EngineVersions,
    ErrorInfo,
    JobStatus,
    OutputMeta,
    PublishStage,
    PublishStatus,
    PublishTarget,
    RenderStage,
    Trace,
)
from maker8.models.contracts import DLQPayload, RenderRequest, RenderResult
from maker8.models.manifest import Manifest
from maker8.models.spec import RenderSpec

__all__ = [
    # common
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
    # spec
    "RenderSpec",
    # contracts
    "DLQPayload",
    "RenderRequest",
    "RenderResult",
    # manifest
    "Manifest",
]
