"""NORMALIZE stage – re-encode / transcode assets to consistent formats."""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path

from maker8.models.common import AssetWarning, RenderStage
from maker8.observability.helpers import Timer, truncate_stderr
from maker8.observability.metrics import SUBPROCESS_DURATION, SUBPROCESS_FAILURES
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.rendering.encoder import check_nvenc
from maker8.rendering.ffmpeg_runtime import resolve_ffmpeg_binary, resolve_ffprobe_binary
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


def _analyze_ffmpeg_failure_reason(stderr: str) -> str:
    """Analyze stderr to categorize NVENC failure reason.

    Returns one of:
    - "gpu_unavailable" – CUDA/GPU not accessible
    - "audio_only_input" – no video stream found
    - "corrupt_input" – cannot decode input
    - "cuda_decode_failed" – CUDA decoder rejected format
    - "nvenc_encode_failed" – encoding itself failed
    - "unknown" – unknown cause
    """
    stderr_lower = stderr.lower()

    # GPU/CUDA not available
    if any(
        x in stderr_lower
        for x in [
            "cannot load libnvidia-encode",
            "no nvenc capable devices",
            "cannot init cuda",
            "cuda is not available",
            "gpu device not found",
        ]
    ):
        return "gpu_unavailable"

    # Audio-only or no video frame
    if "video:0kib" in stderr_lower or "no video stream" in stderr_lower:
        return "audio_only_input"

    # Input corruption or unreadable
    if any(
        x in stderr_lower
        for x in ["unknown format", "invalid data found", "corrupt data", "premature end of file"]
    ):
        return "corrupt_input"

    # CUDA decode issues
    if any(
        x in stderr_lower
        for x in [
            "unsupported pixel format",
            "decoder not found",
            "hevc nvdec",
            "h264 cuvid",
        ]
    ):
        return "cuda_decode_failed"

    # Generic encode fail
    if "encoding failed" in stderr_lower or "nvenc" in stderr_lower:
        return "nvenc_encode_failed"

    return "unknown"


def _has_video_stream(path: Path) -> bool:
    """Return ``True`` if *path* contains at least one video stream."""
    ffprobe = resolve_ffprobe_binary()
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
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


_MIN_VALID_VIDEO_BYTES = 1024  # a real MP4 with even one frame is > 1 KiB
_MIN_VALID_AUDIO_BYTES = 256


def _is_valid_media(path: Path, *, min_bytes: int = _MIN_VALID_VIDEO_BYTES) -> bool:
    """Return ``True`` if *path* exists, exceeds *min_bytes*, and ffprobe can read it."""
    if not path.exists():
        return False
    if path.stat().st_size < min_bytes:
        return False
    ffprobe = resolve_ffprobe_binary()
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # ffprobe should return a numeric duration for any valid media
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


def _build_video_cmd(
    src: Path,
    dest: Path,
    *,
    use_nvenc: bool,
    cpu_decode: bool = False,
    proxy_max_short_edge: int = 0,
) -> list[str]:
    """Build the FFmpeg command for video normalisation.

    When *proxy_max_short_edge* > 0 a scale filter is injected so the
    short edge never exceeds that value (maintains aspect ratio, rounds
    to even dimensions).

    When *cpu_decode* is True, FFmpeg will decode using CPU (no CUDA hwaccel)
    even if NVENC is being used for encoding. This is useful as a fallback
    when GPU decode fails but GPU encode might still work.

    **NVENC + proxy interaction:** ``-hwaccel_output_format cuda`` keeps
    decoded frames in GPU memory, but the ``scale`` filter is CPU-only.
    When proxy scaling is required, we omit ``-hwaccel_output_format cuda``
    so FFmpeg auto-transfers frames to system memory for the scale filter,
    then NVENC re-uploads for encoding.  Without proxy the full GPU path
    is used.

    **NVENC + cpu_decode:** When encoding with NVENC but decoding with CPU,
    we skip GPU hwaccel flags entirely to avoid GPU memory pressure.
    """
    ffmpeg = resolve_ffmpeg_binary()

    # Scale filter: "scale=w:h" where the shorter dimension is capped.
    # Expression ensures both width and height are divisible by 2.
    vf: list[str] = []
    if proxy_max_short_edge > 0:
        se = proxy_max_short_edge
        # If source is wider than tall → short edge is height, else width.
        # The expression handles both orientations.
        vf = [
            "-vf",
            (
                f"scale='if(gte(iw,ih),"
                f"if(gt(ih,{se}),trunc(oh*a/2)*2,iw),"
                f"if(gt(iw,{se}),{se},iw))"
                f":if(gte(iw,ih),"
                f"if(gt(ih,{se}),{se},ih),"
                f"if(gt(iw,{se}),trunc(ow/a/2)*2,ih))'"
            ),
        ]

    if use_nvenc:
        if cpu_decode:
            # CPU decode + NVENC encode: no GPU hwaccel
            return [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                *vf,
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p4",
                "-cq",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(dest),
            ]
        # When proxy scaling is needed we must NOT keep frames in CUDA
        # memory (hwaccel_output_format cuda) because the software
        # ``scale`` filter cannot operate on GPU surfaces.
        hwaccel_args: list[str] = ["-hwaccel", "cuda"]
        if not vf:
            # Full GPU path: decode → NVENC encode, no CPU filters.
            hwaccel_args += ["-hwaccel_output_format", "cuda"]

        return [
            ffmpeg,
            "-y",
            *hwaccel_args,
            "-i",
            str(src),
            *vf,
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p4",
            "-cq",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    return [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        *vf,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(dest),
    ]


class NormalizeStage(Stage):
    def __init__(self, proxy_max_short_edge: int = 0) -> None:
        self._proxy_max_short_edge = proxy_max_short_edge

    @property
    def name(self) -> RenderStage:
        return RenderStage.NORMALIZE

    def execute(self, ctx: PipelineContext) -> None:
        ctx.ensure_dirs()

        # On retry, purge stale normalized_assets entries whose files are
        # missing or corrupt (e.g. partial write from a killed FFmpeg).
        if ctx.attempt > 1:
            stale = [aid for aid, p in ctx.normalized_assets.items() if not _is_valid_media(p)]
            for aid in stale:
                stale_path = ctx.normalized_assets.pop(aid)
                stale_path.unlink(missing_ok=True)
                log.warning(
                    "normalize.stale_artifact_purged",
                    job_id=ctx.job_id,
                    asset_id=aid,
                    path=str(stale_path),
                    attempt=ctx.attempt,
                )

        for asset in ctx.render_spec.assets:
            src = ctx.downloaded_assets.get(asset.id)
            if src is None:
                continue
            if asset.id in ctx.normalized_assets:
                continue  # already done (validated above on retry)
            if asset.id in ctx.failed_assets:
                continue  # already failed in a previous stage

            # ── Full-file normalize path ───────────────────────────────
            try:
                normalised = self._normalize_asset(
                    asset.id,
                    asset.type,
                    src,
                    ctx.assets_dir,
                    ctx.job_id,
                )
                ctx.normalized_assets[asset.id] = normalised
                log.info(
                    "normalize.asset.success",
                    job_id=ctx.job_id,
                    asset_id=asset.id,
                    asset_type=asset.type,
                    path=str(normalised),
                )
            except StageError as exc:
                # Isolate per-asset failure: record warning, mark as failed,
                # and continue with remaining assets.
                ctx.failed_assets.add(asset.id)
                ctx.warnings.append(
                    AssetWarning(
                        asset_id=asset.id,
                        stage="NORMALIZE",
                        code=exc.code,
                        message=str(exc),
                        fallback_used="asset_skipped",
                    )
                )
                log.warning(
                    "normalize.asset.skipped",
                    job_id=ctx.job_id,
                    asset_id=asset.id,
                    asset_type=asset.type,
                    error_code=exc.code,
                    error_message=str(exc),
                    retryable=exc.retryable,
                )

    def _normalize_asset(
        self,
        asset_id: str,
        asset_type: str,
        src: Path,
        dest_dir: Path,
        job_id: str,
    ) -> Path:
        """Dispatch normalization by asset type."""
        # Log stream metadata before processing
        if asset_type == "video":
            ffprobe = resolve_ffprobe_binary()
            try:
                result = subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=index,codec_type,codec_name,width,height,duration",
                        "-of",
                        "json",
                        str(src),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    log.info(
                        "normalize.stream_metadata",
                        job_id=job_id,
                        asset_id=asset_id,
                        stream_info=result.stdout[:1000],  # truncate to avoid huge logs
                    )
            except Exception:
                pass  # Metadata logging is optional

        if asset_type == "video":
            if _has_video_stream(src):
                return self._normalize_video(
                    src,
                    dest_dir,
                    job_id,
                    asset_id,
                    proxy_max_short_edge=self._proxy_max_short_edge,
                )
            log.warning(
                "normalize.no_video_stream",
                job_id=job_id,
                asset_id=asset_id,
                path=str(src),
            )
            return self._normalize_audio(src, dest_dir, job_id, asset_id)
        if asset_type == "audio":
            return self._normalize_audio(src, dest_dir, job_id, asset_id)
        # Images – no normalisation needed
        return src

    # ── FFmpeg helpers ───────────────────────────────────────────────

    @staticmethod
    def _normalize_video(
        src: Path,
        dest_dir: Path,
        job_id: str,
        asset_id: str,
        *,
        proxy_max_short_edge: int = 0,
    ) -> Path:
        """Re-encode to H.264 + AAC in an MP4 container.

        Attempts GPU-accelerated encoding via NVENC when available,
        falling back transparently to software ``libx264`` on failure.
        When *proxy_max_short_edge* > 0, the video is downscaled so its
        short edge never exceeds that value.
        """
        dest = dest_dir / f"{src.stem}_norm.mp4"
        if _is_valid_media(dest):
            log.info(
                "normalize.reuse_existing",
                job_id=job_id,
                asset_id=asset_id,
                path=str(dest),
                size_bytes=dest.stat().st_size,
            )
            return dest
        # Remove any partial/corrupt leftover before encoding
        dest.unlink(missing_ok=True)

        use_nvenc = check_nvenc()
        encoder = "h264_nvenc" if use_nvenc else "libx264"
        cmd = _build_video_cmd(
            src,
            dest,
            use_nvenc=use_nvenc,
            proxy_max_short_edge=proxy_max_short_edge,
        )

        timer = Timer().start()
        log.info(
            "subprocess.start",
            job_id=job_id,
            asset_id=asset_id,
            executable="ffmpeg",
            operation="normalize_video",
            encoder=encoder,
            proxy_max_short_edge=proxy_max_short_edge,
            input=src.name,
            output=dest.name,
        )
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=_VIDEO_TIMEOUT,
            )
            timer.stop()
            SUBPROCESS_DURATION.labels(stage="NORMALIZE", source_kind="ffmpeg").observe(
                timer.elapsed_sec
            )
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
            # If NVENC failed, attempt intermediate retry: CPU decode + NVENC encode
            if use_nvenc:
                failure_reason = _analyze_ffmpeg_failure_reason(exc.stderr)

                # Only retry with CPU decode if the failure was clearly GPU decode-related
                if failure_reason == "cuda_decode_failed":
                    log.info(
                        "normalize.nvenc_retry_cpu_decode",
                        job_id=job_id,
                        asset_id=asset_id,
                        reason=failure_reason,
                    )
                    dest.unlink(missing_ok=True)
                    return NormalizeStage._normalize_video_cpu_decode_nvenc(
                        src,
                        dest_dir,
                        job_id,
                        asset_id,
                        proxy_max_short_edge=proxy_max_short_edge,
                    )

                log.warning(
                    "normalize.nvenc_fallback",
                    job_id=job_id,
                    asset_id=asset_id,
                    returncode=exc.returncode,
                    fallback_encoder="libx264",
                    reason=failure_reason,
                    stderr=truncate_stderr(exc.stderr),
                )
                dest.unlink(missing_ok=True)
                return NormalizeStage._normalize_video_sw(
                    src,
                    dest_dir,
                    job_id,
                    asset_id,
                    proxy_max_short_edge=proxy_max_short_edge,
                )
            SUBPROCESS_FAILURES.labels(stage="NORMALIZE", source_kind="ffmpeg").inc()
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
                signal_name=signal.Signals(-exc.returncode).name if exc.returncode < 0 else None,
                external_kill=killed,
                stderr=stderr,
                duration_sec=timer.elapsed_sec,
            )
            dest.unlink(missing_ok=True)
            raise StageError(
                RenderStage.NORMALIZE,
                error_code,
                f"FFmpeg normalisation failed for {src.name} (rc={exc.returncode}): {stderr}",
                retryable=killed,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            timer.stop()
            dest.unlink(missing_ok=True)
            SUBPROCESS_FAILURES.labels(stage="NORMALIZE", source_kind="ffmpeg").inc()
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
                RenderStage.NORMALIZE,
                "FFMPEG_TIMEOUT",
                f"FFmpeg video normalisation timed out after {exc.timeout}s for {src.name}",
                retryable=False,
            ) from exc
        return dest

    @staticmethod
    def _normalize_video_sw(
        src: Path,
        dest_dir: Path,
        job_id: str,
        asset_id: str,
        *,
        proxy_max_short_edge: int = 0,
    ) -> Path:
        """Software-only H.264 fallback (no GPU)."""
        dest = dest_dir / f"{src.stem}_norm.mp4"
        dest.unlink(missing_ok=True)
        cmd = _build_video_cmd(
            src,
            dest,
            use_nvenc=False,
            proxy_max_short_edge=proxy_max_short_edge,
        )

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
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=_VIDEO_TIMEOUT,
            )
            timer.stop()
            SUBPROCESS_DURATION.labels(stage="NORMALIZE", source_kind="ffmpeg").observe(
                timer.elapsed_sec
            )
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
            SUBPROCESS_FAILURES.labels(stage="NORMALIZE", source_kind="ffmpeg").inc()
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
            dest.unlink(missing_ok=True)
            raise StageError(
                RenderStage.NORMALIZE,
                "FFMPEG_TIMEOUT",
                f"FFmpeg video normalisation timed out after {exc.timeout}s for {src.name}",
                retryable=False,
            ) from exc
        except subprocess.CalledProcessError as exc:
            timer.stop()
            dest.unlink(missing_ok=True)
            SUBPROCESS_FAILURES.labels(stage="NORMALIZE", source_kind="ffmpeg").inc()
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
                signal_name=signal.Signals(-exc.returncode).name if exc.returncode < 0 else None,
                external_kill=killed,
                stderr=stderr,
                duration_sec=timer.elapsed_sec,
            )
            raise StageError(
                RenderStage.NORMALIZE,
                error_code,
                f"FFmpeg normalisation failed for {src.name} (rc={exc.returncode}): {stderr}",
                retryable=killed,
            ) from exc
        return dest

    @staticmethod
    def _normalize_video_cpu_decode_nvenc(
        src: Path,
        dest_dir: Path,
        job_id: str,
        asset_id: str,
        *,
        proxy_max_short_edge: int = 0,
    ) -> Path:
        """CPU decode + NVENC encode (intermediate retry after GPU decode failed).

        This is an intermediate fallback between full GPU path and software-only.
        Used when GPU decode fails but NVENC encoder might still work.
        """
        dest = dest_dir / f"{src.stem}_norm.mp4"
        dest.unlink(missing_ok=True)
        cmd = _build_video_cmd(
            src,
            dest,
            use_nvenc=True,
            cpu_decode=True,
            proxy_max_short_edge=proxy_max_short_edge,
        )

        timer = Timer().start()
        log.info(
            "subprocess.start",
            job_id=job_id,
            asset_id=asset_id,
            executable="ffmpeg",
            operation="normalize_video",
            encoder="h264_nvenc",
            decode_mode="cpu",
            input=src.name,
            output=dest.name,
        )
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=_VIDEO_TIMEOUT,
            )
            timer.stop()
            SUBPROCESS_DURATION.labels(stage="NORMALIZE", source_kind="ffmpeg").observe(
                timer.elapsed_sec
            )
            log.info(
                "subprocess.success",
                job_id=job_id,
                asset_id=asset_id,
                executable="ffmpeg",
                operation="normalize_video",
                encoder="h264_nvenc",
                decode_mode="cpu",
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
                encoder="h264_nvenc",
                decode_mode="cpu",
                reason="timeout",
                timeout_sec=exc.timeout,
                duration_sec=timer.elapsed_sec,
            )
            dest.unlink(missing_ok=True)
            raise StageError(
                RenderStage.NORMALIZE,
                "FFMPEG_TIMEOUT",
                f"FFmpeg video normalisation timed out after {exc.timeout}s for {src.name}",
                retryable=False,
            ) from exc
        except subprocess.CalledProcessError as exc:
            timer.stop()
            dest.unlink(missing_ok=True)
            SUBPROCESS_FAILURES.labels(stage="NORMALIZE", source_kind="ffmpeg").inc()
            stderr = truncate_stderr(exc.stderr)

            # If CPU decode + NVENC also fails, fall back to software encoding
            log.warning(
                "normalize.cpu_decode_nvenc_fallback",
                job_id=job_id,
                asset_id=asset_id,
                returncode=exc.returncode,
                fallback_encoder="libx264",
                reason="cpu_decode_nvenc_failed",
                stderr=stderr,
            )
            return NormalizeStage._normalize_video_sw(
                src,
                dest_dir,
                job_id,
                asset_id,
                proxy_max_short_edge=proxy_max_short_edge,
            )
        return dest

    @staticmethod
    def _normalize_audio(src: Path, dest_dir: Path, job_id: str, asset_id: str) -> Path:
        """Convert to mono 44.1 kHz WAV for consistent MoviePy handling."""
        dest = dest_dir / f"{src.stem}_norm.wav"
        if _is_valid_media(dest, min_bytes=_MIN_VALID_AUDIO_BYTES):
            log.info(
                "normalize.reuse_existing",
                job_id=job_id,
                asset_id=asset_id,
                path=str(dest),
                size_bytes=dest.stat().st_size,
            )
            return dest
        dest.unlink(missing_ok=True)

        cmd = [
            resolve_ffmpeg_binary(),
            "-y",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "44100",
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
            SUBPROCESS_DURATION.labels(stage="NORMALIZE", source_kind="ffmpeg").observe(
                timer.elapsed_sec
            )
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
            dest.unlink(missing_ok=True)
            raise StageError(
                RenderStage.NORMALIZE,
                "FFMPEG_TIMEOUT",
                f"FFmpeg audio normalisation timed out after {exc.timeout}s for {src.name}",
                retryable=False,
            ) from exc
        except subprocess.CalledProcessError as exc:
            timer.stop()
            dest.unlink(missing_ok=True)
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
                signal_name=signal.Signals(-exc.returncode).name if exc.returncode < 0 else None,
                external_kill=killed,
                stderr=stderr,
            )
            raise StageError(
                RenderStage.NORMALIZE,
                error_code,
                f"Audio normalisation failed for {src.name} (rc={exc.returncode}): {stderr}",
                retryable=killed,
            ) from exc
        return dest
