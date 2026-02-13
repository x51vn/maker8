"""Pipeline orchestrator – chains stages, handles retry and DLQ.

The ``Orchestrator`` is the top-level entry point called by ``app.py``
for every inbound Kafka message.
"""

from __future__ import annotations

import shutil
import subprocess

from maker8.config import Settings
from maker8.kafka.producer import KafkaProducer
from maker8.models.common import (
    EngineVersions,
    ErrorInfo,
    JobStatus,
    RenderStage,
)
from maker8.models.contracts import DLQPayload, RenderRequest, RenderResult
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.download import DownloadStage
from maker8.pipeline.emit import EmitResultStage
from maker8.pipeline.normalize import NormalizeStage
from maker8.pipeline.render import RenderStageImpl
from maker8.pipeline.resolve import ResolveAssetsStage
from maker8.pipeline.stage import Stage
from maker8.pipeline.tts import TTSStage
from maker8.pipeline.upload import UploadDropboxStage
from maker8.pipeline.validate import ValidateStage
from maker8.plugins.registry import PluginRegistry
from maker8.retry import RetryPolicy, StageError
from maker8.services.dropbox_client import DropboxClient
from maker8.services.tts_client import TTSService
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class Orchestrator:
    """Build the stage chain and run it for one ``RenderRequest``."""

    def __init__(
        self,
        settings: Settings,
        producer: KafkaProducer,
        registry: PluginRegistry,
        tts_service: TTSService,
        dbx_client: DropboxClient,
    ) -> None:
        self._settings = settings
        self._producer = producer
        self._retry_policy = RetryPolicy(
            max_attempts=settings.render_max_attempts,
            min_delay_sec=settings.render_retry_min_delay_sec,
            max_delay_sec=settings.render_retry_max_delay_sec,
        )

        # ── Build stage list (order matters) ─────────────────────────
        self._stages: list[Stage] = [
            ValidateStage(),
            ResolveAssetsStage(registry),
            DownloadStage(registry),
            NormalizeStage(),
            TTSStage(tts_service),
            RenderStageImpl(),
            UploadDropboxStage(dbx_client),
            EmitResultStage(producer, settings.kafka_render_result_topic),
        ]

    # ── Public entry point ───────────────────────────────────────────

    def handle(self, payload: dict) -> None:
        """Parse the Kafka message and run the full pipeline."""
        try:
            request = RenderRequest.model_validate(payload)
        except Exception:
            log.exception("orchestrator.invalid_payload")
            return  # cannot DLQ without a valid job_id

        ctx = PipelineContext.from_request(
            job_id=request.job_id,
            render_spec=request.render_spec,
            trace=request.trace,
            base_work_dir=self._settings.work_dir,
        )

        log.info("orchestrator.start", job_id=ctx.job_id)

        try:
            self._run_stages(ctx)
            log.info("orchestrator.done", job_id=ctx.job_id, job_key=ctx.job_key)
        except StageError as exc:
            log.error(
                "orchestrator.failed",
                job_id=ctx.job_id,
                stage=exc.stage.value,
                code=exc.code,
                message=str(exc),
            )
            self._send_failed_result(ctx, exc)
            self._send_dlq(ctx, exc)
        finally:
            self._cleanup(ctx)

    # ── Stage runner with per-stage retry ────────────────────────────

    def _run_stages(self, ctx: PipelineContext) -> None:
        for stage in self._stages:
            self._execute_with_retry(stage, ctx)

    def _execute_with_retry(self, stage: Stage, ctx: PipelineContext) -> None:
        policy = self._retry_policy
        last_exc: StageError | None = None

        for attempt in range(1, policy.max_attempts + 1):
            try:
                stage.execute(ctx)
                return  # success
            except StageError as exc:
                last_exc = exc
                if not policy.is_retryable(stage.name) or not exc.retryable:
                    raise
                if attempt >= policy.max_attempts:
                    raise
                log.warning(
                    "orchestrator.retry",
                    stage=stage.name.value,
                    attempt=attempt,
                    max=policy.max_attempts,
                    delay=policy.delay(attempt),
                )
                policy.sleep(attempt)

        # Should not reach here, but just in case
        if last_exc:
            raise last_exc

    # ── Failure handling ─────────────────────────────────────────────

    def _send_failed_result(self, ctx: PipelineContext, exc: StageError) -> None:
        """Best-effort: produce a FAILED RenderResult."""
        try:
            from maker8.models.contracts import DropboxOutput

            result = RenderResult(
                job_id=ctx.job_id,
                status=JobStatus.FAILED,
                job_key=ctx.job_key,
                output_meta=ctx.output_meta,
                engine_versions=_collect_engine_versions(),
                error=ErrorInfo(
                    code=exc.code,
                    stage=exc.stage.value,
                    retryable=exc.retryable,
                    message=str(exc),
                ),
            )
            payload = result.model_dump(mode="json", by_alias=True)
            self._producer.send(
                self._settings.kafka_render_result_topic,
                key=ctx.job_id,
                value=payload,
            )
        except Exception:
            log.exception("orchestrator.failed_result_emit_error")

    def _send_dlq(self, ctx: PipelineContext, exc: StageError) -> None:
        """Produce a DLQ message."""
        try:
            dlq = DLQPayload(
                job_id=ctx.job_id,
                job_key=ctx.job_key,
                failed_stage=exc.stage.value,
                attempts=self._retry_policy.max_attempts,
                last_error=ErrorInfo(
                    code=exc.code,
                    stage=exc.stage.value,
                    retryable=exc.retryable,
                    message=str(exc),
                ),
                dropbox={"video_path": ctx.dropbox_video_ref.path}
                if ctx.dropbox_video_ref
                else {},
                trace=ctx.trace,
            )
            payload = dlq.model_dump(mode="json", by_alias=True)
            self._producer.send(
                self._settings.kafka_render_dlq_topic,
                key=ctx.job_id,
                value=payload,
            )
        except Exception:
            log.exception("orchestrator.dlq_emit_error")

    # ── Cleanup ──────────────────────────────────────────────────────

    @staticmethod
    def _cleanup(ctx: PipelineContext) -> None:
        """Remove the job work directory."""
        try:
            if ctx.work_dir.exists():
                shutil.rmtree(ctx.work_dir)
        except Exception:
            log.exception("orchestrator.cleanup_error", work_dir=str(ctx.work_dir))


# ── Shared helper (also used by upload + emit stages) ────────────────────────


def _collect_engine_versions() -> EngineVersions:
    """Detect installed versions of MoviePy, FFmpeg, and yt-dlp."""
    versions = EngineVersions()

    try:
        import moviepy

        versions.moviepy = getattr(moviepy, "__version__", "unknown")
    except ImportError:
        pass

    try:
        out = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        first_line = out.stdout.split("\n", 1)[0]
        versions.ffmpeg = first_line.split(" ")[2] if len(first_line.split(" ")) > 2 else first_line
    except Exception:
        pass

    try:
        out = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        versions.youtube_dlp = out.stdout.strip()
    except Exception:
        pass

    return versions
