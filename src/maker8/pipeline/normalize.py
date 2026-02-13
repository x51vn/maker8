"""NORMALIZE stage – re-encode / transcode assets to consistent formats."""

from __future__ import annotations

import subprocess
from pathlib import Path

from maker8.models.common import RenderStage
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class NormalizeStage(Stage):
    @property
    def name(self) -> RenderStage:
        return RenderStage.NORMALIZE

    def execute(self, ctx: PipelineContext) -> None:
        ctx.ensure_dirs()

        for asset in ctx.render_spec.assets:
            src = ctx.downloaded_assets.get(asset.id)
            if src is None:
                continue
            if asset.id in ctx.normalized_assets:
                continue  # already done

            if asset.type == "video":
                normalised = self._normalize_video(src, ctx.assets_dir)
            elif asset.type == "audio":
                normalised = self._normalize_audio(src, ctx.assets_dir)
            else:
                # Images – no normalisation needed
                normalised = src

            ctx.normalized_assets[asset.id] = normalised
            log.info("normalize.ok", asset_id=asset.id, path=str(normalised))

    # ── FFmpeg helpers ───────────────────────────────────────────────

    @staticmethod
    def _normalize_video(src: Path, dest_dir: Path) -> Path:
        """Re-encode to H.264 + AAC in an MP4 container."""
        dest = dest_dir / f"{src.stem}_norm.mp4"
        if dest.exists():
            return dest

        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(dest),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        except subprocess.CalledProcessError as exc:
            raise StageError(
                RenderStage.NORMALIZE, "FFMPEG_ERROR",
                f"FFmpeg normalisation failed for {src.name}: {exc.stderr}",
                retryable=False,
            ) from exc
        return dest

    @staticmethod
    def _normalize_audio(src: Path, dest_dir: Path) -> Path:
        """Convert to mono 44.1 kHz WAV for consistent MoviePy handling."""
        dest = dest_dir / f"{src.stem}_norm.wav"
        if dest.exists():
            return dest

        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-ac", "1", "-ar", "44100",
            str(dest),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
        except subprocess.CalledProcessError as exc:
            raise StageError(
                RenderStage.NORMALIZE, "FFMPEG_ERROR",
                f"Audio normalisation failed for {src.name}: {exc.stderr}",
                retryable=False,
            ) from exc
        return dest
