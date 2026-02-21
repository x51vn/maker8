"""Mutable context that flows through every pipeline stage.

``PipelineContext`` is a plain ``dataclass`` – it is *not* serialised to JSON.
Use it to carry intermediate artefacts (local file paths, TTS durations, etc.)
between stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maker8.models.common import DropboxFileRef, OutputMeta, Trace
from maker8.models.spec import RenderSpec


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

    # ── Dropbox refs ─────────────────────────────────────────────────
    dropbox_video_ref: DropboxFileRef | None = None
    dropbox_manifest_ref: DropboxFileRef | None = None

    # ── Retry tracking ───────────────────────────────────────────────
    attempt: int = 1

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_request(
        cls,
        job_id: str,
        render_spec: RenderSpec,
        trace: Trace,
        base_work_dir: Path,
    ) -> PipelineContext:
        wd = base_work_dir / job_id
        return cls(
            job_id=job_id,
            render_spec=render_spec,
            trace=trace,
            work_dir=wd,
            assets_dir=wd / "assets",
            tts_dir=wd / "tts",
            output_dir=wd / "output",
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
