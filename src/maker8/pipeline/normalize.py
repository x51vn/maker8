"""NORMALIZE stage – re-encode / transcode assets to consistent formats."""

from __future__ import annotations

import subprocess
from pathlib import Path

from maker8.models.common import RenderStage
from maker8.observability.helpers import Timer, truncate_stderr
from maker8.observability.metrics import SUBPROCESS_DURATION, SUBPROCESS_FAILURES
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
                normalised = self._normalize_video(src, ctx.assets_dir, ctx.job_id, asset.id)
            elif asset.type == "audio":
                normalised = self._normalize_audio(src, ctx.assets_dir, ctx.job_id, asset.id)
            else:
                # Images – no normalisation needed
                normalised = src

            ctx.normalized_assets[asset.id] = normalised
            log.info(
                "normalize.asset.success",
                job_id=ctx.job_id,
                asset_id=asset.id,
                asset_type=asset.type,
                path=str(normalised),
            )

    # ── FFmpeg helpers ───────────────────────────────────────────────

    @staticmethod
    def _normalize_video(
        src: Path, dest_dir: Path, job_id: str, asset_id: str
    ) -> Path:
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
        timer = Timer().start()
        log.info(
            "subprocess.start",
            job_id=job_id,
            asset_id=asset_id,
            executable="ffmpeg",
            operation="normalize_video",
            input=src.name,
            output=dest.name,
        )
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
            timer.stop()
            SUBPROCESS_DURATION.labels(
                stage="NORMALIZE", source_kind="ffmpeg"
            ).observe(timer.elapsed_sec)
            log.info(
                "subprocess.success",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_video",
                duration_sec=timer.elapsed_sec,
            )
        except subprocess.TimeoutExpired as exc:
            timer.stop()
            SUBPROCESS_FAILURES.labels(stage="NORMALIZE", source_kind="ffmpeg").inc()
            log.error(
                "subprocess.failure",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_video",
                reason="timeout",
                timeout_sec=exc.timeout,
                duration_sec=timer.elapsed_sec,
            )
            raise StageError(
                RenderStage.NORMALIZE, "FFMPEG_TIMEOUT",
                f"FFmpeg video normalisation timed out after {exc.timeout}s for {src.name}",
                retryable=False,
            ) from exc
        except subprocess.CalledProcessError as exc:
            timer.stop()
            SUBPROCESS_FAILURES.labels(stage="NORMALIZE", source_kind="ffmpeg").inc()
            stderr = truncate_stderr(exc.stderr)
            log.error(
                "subprocess.failure",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_video",
                returncode=exc.returncode,
                stderr=stderr,
                duration_sec=timer.elapsed_sec,
            )
            raise StageError(
                RenderStage.NORMALIZE, "FFMPEG_ERROR",
                f"FFmpeg normalisation failed for {src.name} (rc={exc.returncode}): {stderr}",
                retryable=False,
            ) from exc
        return dest

    @staticmethod
    def _normalize_audio(
        src: Path, dest_dir: Path, job_id: str, asset_id: str
    ) -> Path:
        """Convert to mono 44.1 kHz WAV for consistent MoviePy handling."""
        dest = dest_dir / f"{src.stem}_norm.wav"
        if dest.exists():
            return dest

        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-ac", "1", "-ar", "44100",
            str(dest),
        ]
        timer = Timer().start()
        log.info(
            "subprocess.start",
            job_id=job_id,
            asset_id=asset_id,
            executable="ffmpeg",
            operation="normalize_audio",
            input=src.name,
        )
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
            timer.stop()
            SUBPROCESS_DURATION.labels(
                stage="NORMALIZE", source_kind="ffmpeg"
            ).observe(timer.elapsed_sec)
            log.info(
                "subprocess.success",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_audio",
                duration_sec=timer.elapsed_sec,
            )
        except subprocess.TimeoutExpired as exc:
            timer.stop()
            SUBPROCESS_FAILURES.labels(stage="NORMALIZE", source_kind="ffmpeg").inc()
            log.error(
                "subprocess.failure",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_audio",
                reason="timeout",
                timeout_sec=exc.timeout,
            )
            raise StageError(
                RenderStage.NORMALIZE, "FFMPEG_TIMEOUT",
                f"FFmpeg audio normalisation timed out after {exc.timeout}s for {src.name}",
                retryable=False,
            ) from exc
        except subprocess.CalledProcessError as exc:
            timer.stop()
            SUBPROCESS_FAILURES.labels(stage="NORMALIZE", source_kind="ffmpeg").inc()
            stderr = truncate_stderr(exc.stderr)
            log.error(
                "subprocess.failure",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_audio",
                returncode=exc.returncode,
                stderr=stderr,
            )
            raise StageError(
                RenderStage.NORMALIZE, "FFMPEG_ERROR",
                f"Audio normalisation failed for {src.name} (rc={exc.returncode}): {stderr}",
                retryable=False,
            ) from exc
        return dest
