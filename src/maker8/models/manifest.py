"""Manifest JSON written alongside the rendered video on Dropbox."""

from __future__ import annotations

from pydantic import BaseModel, Field

from maker8.models.common import (
    DropboxFileRef,
    EngineVersions,
    OutputMeta,
    PublishTarget,
)
from render_contracts.render_spec import UploaderMetadata


class ManifestDropbox(BaseModel):
    video: DropboxFileRef = Field(default_factory=DropboxFileRef)


class Manifest(BaseModel):
    """``<job_id>.manifest.json`` stored next to the mp4 on Dropbox."""

    job_id: str
    job_key: str = ""
    dry_run: bool = False
    canvas_profile: str | None = None
    dropbox: ManifestDropbox = Field(default_factory=ManifestDropbox)
    output_meta: OutputMeta = Field(default_factory=OutputMeta)
    uploader_metadata: UploaderMetadata = Field(default_factory=UploaderMetadata)
    publish_targets: list[PublishTarget] = Field(default_factory=list)
    engine_versions: EngineVersions = Field(default_factory=EngineVersions)
