"""Mutable context that flows through every pipeline stage.

``PipelineContext`` is a plain ``dataclass`` – it is *not* serialised to JSON.
Use it to carry intermediate artefacts (local file paths, TTS durations, etc.)
between stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maker8.models.common import AssetWarning, DropboxFileRef, OutputMeta, Trace
from maker8.models.contracts import ResultDestination
from maker8.models.spec import RenderSpec, UploaderMetadata

# Pattern for safe job IDs: lowercase alphanumeric, hyphens, underscores
_SAFE_JOB_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789-_"
)

# ── TTS result (pipeline-internal) ──────────────────────────────────────────


@dataclass
class TTSResult:
    """One narration audio file produced by the TTS stage."""

    scene_id: str
    audio_path: Path
    duration_sec: float


# ── Pipeline context ─────────────────────────────────────────────────────────


@dataclass
class PipelineContext:
    job_id: str
    render_spec: RenderSpec
    job_key: str = ""
    trace: Trace = field(default_factory=Trace)

    # ── Request-level flags ──────────────────────────────────────────
    dry_run: bool = False
    canvas_profile: str | None = None
    uploader_metadata: UploaderMetadata = field(default_factory=UploaderMetadata)

    # ── Directories (created per job) ────────────────────────────────
    work_dir: Path = field(default_factory=lambda: Path("/tmp/maker8"))
    assets_dir: Path = field(default_factory=lambda: Path("/tmp/maker8/assets"))
    tts_dir: Path = field(default_factory=lambda: Path("/tmp/maker8/tts"))
    output_dir: Path = field(default_factory=lambda: Path("/tmp/maker8/output"))

    # ── Stage outputs ────────────────────────────────────────────────
    resolved_plans: dict[str, Any] = field(default_factory=dict)  # asset_id → ResolvedAssetPlan
    downloaded_assets: dict[str, Path] = field(default_factory=dict)
    normalized_assets: dict[str, Path] = field(default_factory=dict)
    tts_results: dict[str, TTSResult] = field(default_factory=dict)

    rendered_video: Path | None = None
    output_meta: OutputMeta = field(default_factory=OutputMeta)

    # ── Asset report (carried into RenderResult) ─────────────────────
    asset_report: list[dict[str, Any]] = field(default_factory=list)

    # ── Dropbox refs ─────────────────────────────────────────────────
    dropbox_video_ref: DropboxFileRef | None = None
    dropbox_manifest_ref: DropboxFileRef | None = None
    # ── Result destination (per-request routing) ────────────────
    result_destination: ResultDestination | None = None
    # ── Retry tracking ───────────────────────────────────────────────
    attempt: int = 1

    # ── Degradation tracking ─────────────────────────────────────────
    warnings: list[AssetWarning] = field(default_factory=list)
    failed_assets: set[str] = field(default_factory=set)
    skipped_scenes: set[str] = field(default_factory=set)

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_request(
        cls,
        job_id: str,
        render_spec: RenderSpec,
        trace: Trace,
        base_work_dir: Path,
        *,
        dry_run: bool = False,
        canvas_profile: str | None = None,
        uploader_metadata: UploaderMetadata | None = None,
        result_destination: ResultDestination | None = None,
    ) -> PipelineContext:
        # Validate job_id to prevent path traversal attacks
        if not job_id or not all(c in _SAFE_JOB_ID_CHARS for c in job_id):
            raise ValueError(
                f"Invalid job_id: must contain only [a-zA-Z0-9_-], got {job_id!r}"
            )
        wd = base_work_dir / job_id
        return cls(
            job_id=job_id,
            render_spec=render_spec,
            trace=trace,
            work_dir=wd,
            assets_dir=wd / "assets",
            tts_dir=wd / "tts",
            output_dir=wd / "output",
            dry_run=dry_run,
            canvas_profile=canvas_profile,
            uploader_metadata=uploader_metadata or UploaderMetadata(),
            result_destination=result_destination,
        )

    def ensure_dirs(self) -> None:
        """Create all working directories (idempotent).

        Mode 0o700 restricts each per-job directory to the process owner only,
        preventing other local users from reading job assets (video, audio, TTS).
        """
        for d in (self.work_dir, self.assets_dir, self.tts_dir, self.output_dir):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)

    def asset_path(self, asset_id: str) -> Path | None:
        """Prefer normalised file, fall back to downloaded file."""
        return self.normalized_assets.get(asset_id) or self.downloaded_assets.get(asset_id)

    @property
    def is_degraded(self) -> bool:
        """``True`` when the job completed with at least one tolerated failure."""
        return bool(self.warnings)
