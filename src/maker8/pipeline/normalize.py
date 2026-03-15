"""NORMALIZE stage – re-encode / transcode assets to consistent formats."""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path

from maker8.models.common import RenderStage
from maker8.observability.helpers import Timer, truncate_stderr
from maker8.observability.metrics import SUBPROCESS_DURATION, SUBPROCESS_FAILURES
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.rendering.encoder import check_nvenc
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_VIDEO_TIMEOUT = 600  # seconds per video asset
_AUDIO_TIMEOUT = 120  # seconds per audio asset

# Signals that indicate an external kill (OOM, cgroup, operator) rather than
# a normal FFmpeg processing error.  Negative return codes = -(signal_number).
_EXTERNAL_KILL_SIGNALS: frozenset[int] = frozenset(
    {signal.SIGKILL, signal.SIGTERM, signal.SIGXCPU, signal.SIGXFSZ}
)


def _is_external_kill(returncode: int) -> bool:
    """Return ``True`` when *returncode* indicates the process was killed externally."""
    return returncode < 0 and (-returncode) in _EXTERNAL_KILL_SIGNALS


def _has_video_stream(path: Path) -> bool:
    """Return ``True`` if *path* contains at least one video stream."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return "video" in result.stdout
    except Exception:
        # If probe fails, assume there is a video stream and let FFmpeg
        # deal with it downstream.
        return True


def _build_video_cmd(src: Path, dest: Path, *, use_nvenc: bool) -> list[str]:
    """Build the FFmpeg command for video normalisation."""
    if use_nvenc:
        return [
            "ffmpeg", "-y",
            "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
            "-i", str(src),
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(dest),
        ]
    return [
        "ffmpeg", "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dest),
    ]


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
                if _has_video_stream(src):
                    normalised = self._normalize_video(
                        src, ctx.assets_dir, ctx.job_id, asset.id,
                    )
                else:
                    log.warning(
                        "normalize.no_video_stream",
                        job_id=ctx.job_id,
                        asset_id=asset.id,
                        path=str(src),
                    )
                    normalised = self._normalize_audio(
                        src, ctx.assets_dir, ctx.job_id, asset.id,
                    )
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
        """Re-encode to H.264 + AAC in an MP4 container.

        Attempts GPU-accelerated encoding via NVENC when available,
        falling back transparently to software ``libx264`` on failure.
        """
        dest = dest_dir / f"{src.stem}_norm.mp4"
        if dest.exists():
            return dest

        use_nvenc = check_nvenc()
        encoder = "h264_nvenc" if use_nvenc else "libx264"
        cmd = _build_video_cmd(src, dest, use_nvenc=use_nvenc)

        timer = Timer().start()
        log.info(
            "subprocess.start",
            job_id=job_id,
            asset_id=asset_id,
            executable="ffmpeg",
            operation="normalize_video",
            encoder=encoder,
            input=src.name,
            output=dest.name,
        )
        try:
            subprocess.run(
                cmd, check=True, capture_output=True,
                text=True, timeout=_VIDEO_TIMEOUT,
            )
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
                encoder=encoder,
                duration_sec=timer.elapsed_sec,
            )
        except subprocess.CalledProcessError as exc:
            timer.stop()
            # If NVENC failed, fall back to software encoding
            if use_nvenc:
                log.warning(
                    "normalize.nvenc_fallback",
                    job_id=job_id,
                    asset_id=asset_id,
                    returncode=exc.returncode,
                    stderr=truncate_stderr(exc.stderr),
                )
                dest.unlink(missing_ok=True)
                return NormalizeStage._normalize_video_sw(
                    src, dest_dir, job_id, asset_id,
                )
            SUBPROCESS_FAILURES.labels(
                stage="NORMALIZE", source_kind="ffmpeg"
            ).inc()
            stderr = truncate_stderr(exc.stderr)
            killed = _is_external_kill(exc.returncode)
            error_code = "FFMPEG_KILLED" if killed else "FFMPEG_ERROR"
            log.error(
                "subprocess.failure",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_video",
                encoder=encoder,
                returncode=exc.returncode,
                signal_name=signal.Signals(-exc.returncode).name
                if exc.returncode < 0
                else None,
                external_kill=killed,
                stderr=stderr,
                duration_sec=timer.elapsed_sec,
            )
            raise StageError(
                RenderStage.NORMALIZE, error_code,
                f"FFmpeg normalisation failed for {src.name} "
                f"(rc={exc.returncode}): {stderr}",
                retryable=killed,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            timer.stop()
            SUBPROCESS_FAILURES.labels(
                stage="NORMALIZE", source_kind="ffmpeg"
            ).inc()
            log.error(
                "subprocess.failure",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_video",
                encoder=encoder,
                reason="timeout",
                timeout_sec=exc.timeout,
                duration_sec=timer.elapsed_sec,
            )
            raise StageError(
                RenderStage.NORMALIZE, "FFMPEG_TIMEOUT",
                f"FFmpeg video normalisation timed out after "
                f"{exc.timeout}s for {src.name}",
                retryable=False,
            ) from exc
        return dest

    @staticmethod
    def _normalize_video_sw(
        src: Path, dest_dir: Path, job_id: str, asset_id: str
    ) -> Path:
        """Software-only H.264 fallback (no GPU)."""
        dest = dest_dir / f"{src.stem}_norm.mp4"
        dest.unlink(missing_ok=True)
        cmd = _build_video_cmd(src, dest, use_nvenc=False)

        timer = Timer().start()
        log.info(
            "subprocess.start",
            job_id=job_id,
            asset_id=asset_id,
            executable="ffmpeg",
            operation="normalize_video",
            encoder="libx264",
            input=src.name,
            output=dest.name,
        )
        try:
            subprocess.run(
                cmd, check=True, capture_output=True,
                text=True, timeout=_VIDEO_TIMEOUT,
            )
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
                encoder="libx264",
                duration_sec=timer.elapsed_sec,
            )
        except subprocess.TimeoutExpired as exc:
            timer.stop()
            SUBPROCESS_FAILURES.labels(
                stage="NORMALIZE", source_kind="ffmpeg"
            ).inc()
            log.error(
                "subprocess.failure",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_video",
                encoder="libx264",
                reason="timeout",
                timeout_sec=exc.timeout,
                duration_sec=timer.elapsed_sec,
            )
            raise StageError(
                RenderStage.NORMALIZE, "FFMPEG_TIMEOUT",
                f"FFmpeg video normalisation timed out after "
                f"{exc.timeout}s for {src.name}",
                retryable=False,
            ) from exc
        except subprocess.CalledProcessError as exc:
            timer.stop()
            SUBPROCESS_FAILURES.labels(
                stage="NORMALIZE", source_kind="ffmpeg"
            ).inc()
            stderr = truncate_stderr(exc.stderr)
            killed = _is_external_kill(exc.returncode)
            error_code = "FFMPEG_KILLED" if killed else "FFMPEG_ERROR"
            log.error(
                "subprocess.failure",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_video",
                encoder="libx264",
                returncode=exc.returncode,
                signal_name=signal.Signals(-exc.returncode).name
                if exc.returncode < 0
                else None,
                external_kill=killed,
                stderr=stderr,
                duration_sec=timer.elapsed_sec,
            )
            raise StageError(
                RenderStage.NORMALIZE, error_code,
                f"FFmpeg normalisation failed for {src.name} "
                f"(rc={exc.returncode}): {stderr}",
                retryable=killed,
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
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=_AUDIO_TIMEOUT)
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
            killed = _is_external_kill(exc.returncode)
            error_code = "FFMPEG_KILLED" if killed else "FFMPEG_ERROR"
            log.error(
                "subprocess.failure",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_audio",
                returncode=exc.returncode,
                signal_name=signal.Signals(-exc.returncode).name
                if exc.returncode < 0
                else None,
                external_kill=killed,
                stderr=stderr,
            )
            raise StageError(
                RenderStage.NORMALIZE, error_code,
                f"Audio normalisation failed for {src.name} (rc={exc.returncode}): {stderr}",
                retryable=killed,
            ) from exc
        return dest
