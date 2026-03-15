"""Pipeline orchestrator – chains stages, handles retry and DLQ.

The ``Orchestrator`` is the top-level entry point called by ``app.py``
for every inbound Kafka message.
"""

from __future__ import annotations

import shutil
import time
from typing import Any

from maker8.config import Settings
from maker8.kafka.producer import KafkaProducer
from maker8.models.common import (
    ErrorInfo,
    JobStatus,
    RenderStage,
)
from maker8.models.contracts import DLQPayload, DropboxOutput, RenderRequest, RenderResult
from maker8.observability.helpers import Timer
from maker8.observability.metrics import (
    DLQ_EMITTED,
    INVALID_PAYLOAD,
    JOBS_FAILED,
    JOBS_SUCCEEDED,
    JOB_DURATION,
    JOB_IN_PROGRESS,
    RESULT_EMITTED,
    RETRIES_SCHEDULED,
    STAGE_DURATION,
    set_current_stage,
)
from maker8.observability.state import WorkerState
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
from maker8.utils.versions import collect_engine_versions

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
        worker_state: WorkerState | None = None,
    ) -> None:
        self._settings = settings
        self._producer = producer
        self._state = worker_state
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
            RenderStageImpl(registry),
            UploadDropboxStage(dbx_client),
            EmitResultStage(producer, settings.kafka_render_result_topic),
        ]

    # ── Public entry point ───────────────────────────────────────────

    def handle(self, payload: dict[str, Any]) -> None:
        """Parse the Kafka message and run the full pipeline."""
        job_timer = Timer().start()

        try:
            request = RenderRequest.model_validate(payload)
        except Exception:
            log.exception("orchestrator.invalid_payload")
            INVALID_PAYLOAD.inc()
            return  # cannot DLQ without a valid job_id

        ctx = PipelineContext.from_request(
            job_id=request.job_id,
            render_spec=request.render_spec,
            trace=request.trace,
            base_work_dir=self._settings.work_dir,
        )

        correlation_id = ctx.trace.correlation_id if ctx.trace else ""
        log.info(
            "orchestrator.start",
            job_id=ctx.job_id,
            job_key=ctx.job_key,
            correlation_id=correlation_id,
            scenes=len(ctx.render_spec.scenes),
            assets=len(ctx.render_spec.assets),
        )

        JOB_IN_PROGRESS.set(1)
        set_current_stage(None)
        if self._state:
            self._state.on_job_started(ctx.job_id, ctx.job_key)

        try:
            self._run_stages(ctx)
            job_timer.stop()

            log.info(
                "orchestrator.done",
                job_id=ctx.job_id,
                job_key=ctx.job_key,
                correlation_id=correlation_id,
                duration_sec=job_timer.elapsed_sec,
            )
            JOBS_SUCCEEDED.inc()
            JOB_DURATION.labels(status="success").observe(job_timer.elapsed_sec)
            if self._state:
                self._state.on_job_success(ctx.job_id)

        except StageError as exc:
            job_timer.stop()

            log.error(
                "job.failure_summary",
                job_id=ctx.job_id,
                job_key=ctx.job_key,
                correlation_id=correlation_id,
                stage=exc.stage.value,
                attempt=ctx.attempt,
                max_attempts=self._retry_policy.max_attempts,
                error_code=exc.code,
                error_type=type(exc).__name__,
                error_message=str(exc),
                retryable=exc.retryable,
                duration_sec=job_timer.elapsed_sec,
            )
            JOBS_FAILED.labels(stage=exc.stage.value, error_code=exc.code).inc()
            JOB_DURATION.labels(status="failed").observe(job_timer.elapsed_sec)
            if self._state:
                self._state.on_job_failure(ctx.job_id, exc.stage.value, exc.code)

            self._send_failed_result(ctx, exc)
            self._send_dlq(ctx, exc)

        finally:
            JOB_IN_PROGRESS.set(0)
            set_current_stage(None)
            self._cleanup(ctx)

    # ── Stage runner with per-stage retry ────────────────────────────

    def _run_stages(self, ctx: PipelineContext) -> None:
        for stage in self._stages:
            self._execute_with_retry(stage, ctx)

    def _execute_with_retry(self, stage: Stage, ctx: PipelineContext) -> None:
        policy = self._retry_policy
        last_exc: StageError | None = None
        stage_name = stage.name.value

        for attempt in range(1, policy.max_attempts + 1):
            stage_timer = Timer().start()

            set_current_stage(stage_name)
            if self._state:
                self._state.on_stage_enter(stage_name, attempt)

            log.info(
                "stage.start",
                job_id=ctx.job_id,
                stage=stage_name,
                attempt=attempt,
            )

            try:
                stage.execute(ctx)
                stage_timer.stop()

                log.info(
                    "stage.success",
                    job_id=ctx.job_id,
                    stage=stage_name,
                    attempt=attempt,
                    duration_ms=stage_timer.elapsed_ms,
                )
                STAGE_DURATION.labels(stage=stage_name, status="success").observe(
                    stage_timer.elapsed_sec
                )
                return  # success

            except StageError as exc:
                stage_timer.stop()
                last_exc = exc
                ctx.attempt = attempt

                log.warning(
                    "stage.failure",
                    job_id=ctx.job_id,
                    stage=stage_name,
                    attempt=attempt,
                    error_code=exc.code,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    retryable=exc.retryable,
                    duration_ms=stage_timer.elapsed_ms,
                )
                STAGE_DURATION.labels(stage=stage_name, status="failed").observe(
                    stage_timer.elapsed_sec
                )

                if not policy.is_retryable(stage.name) or not exc.retryable:
                    raise
                if attempt >= policy.max_attempts:
                    log.error(
                        "stage.retry_exhausted",
                        job_id=ctx.job_id,
                        stage=stage_name,
                        attempts=attempt,
                        max_attempts=policy.max_attempts,
                        error_code=exc.code,
                        error_message=str(exc),
                    )
                    raise

                delay = policy.delay(attempt)
                log.warning(
                    "stage.retry_scheduled",
                    job_id=ctx.job_id,
                    stage=stage_name,
                    attempt=attempt,
                    max_attempts=policy.max_attempts,
                    delay_sec=delay,
                    error_code=exc.code,
                    error_message=str(exc),
                )
                RETRIES_SCHEDULED.labels(stage=stage_name).inc()
                if self._state:
                    self._state.on_retry_sleep(delay)
                policy.sleep(attempt)

            except Exception as exc:
                stage_timer.stop()
                STAGE_DURATION.labels(stage=stage_name, status="failed").observe(
                    stage_timer.elapsed_sec
                )
                # Wrap unexpected exceptions as non-retryable StageError
                raise StageError(
                    stage.name,
                    "UNEXPECTED_ERROR",
                    f"Unexpected error in {stage_name}: {exc}",
                    retryable=False,
                ) from exc

        # Should not reach here, but just in case
        if last_exc:
            raise last_exc

    # ── Failure handling ─────────────────────────────────────────────

    def _send_failed_result(self, ctx: PipelineContext, exc: StageError) -> None:
        """Best-effort: produce a FAILED RenderResult."""
        try:
            result = RenderResult(
                job_id=ctx.job_id,
                status=JobStatus.FAILED,
                job_key=ctx.job_key,
                output_meta=ctx.output_meta,
                engine_versions=collect_engine_versions(),
                trace=ctx.trace,
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
            RESULT_EMITTED.labels(status="FAILED").inc()
        except Exception:
            log.exception("orchestrator.failed_result_emit_error")

    def _send_dlq(self, ctx: PipelineContext, exc: StageError) -> None:
        """Produce a DLQ message with enriched forensic context."""
        try:
            dlq = DLQPayload(
                job_id=ctx.job_id,
                job_key=ctx.job_key,
                failed_stage=exc.stage.value,
                attempts=ctx.attempt,
                max_attempts=self._retry_policy.max_attempts,
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
                debug_context={
                    "partial_asset_report": ctx.asset_report[:10],
                    "resolved_asset_ids": list(ctx.resolved_plans.keys())[:20],
                    "downloaded_asset_ids": list(ctx.downloaded_assets.keys())[:20],
                    "tts_scene_ids": list(ctx.tts_results.keys())[:20],
                },
            )
            payload = dlq.model_dump(mode="json", by_alias=True)
            self._producer.send(
                self._settings.kafka_render_dlq_topic,
                key=ctx.job_id,
                value=payload,
            )
            DLQ_EMITTED.labels(stage=exc.stage.value).inc()
        except Exception:
            log.exception("orchestrator.dlq_emit_error", job_id=ctx.job_id)

    def _cleanup(self, ctx: PipelineContext) -> None:
        """Best-effort: remove the per-job work directory."""
        wd = ctx.work_dir
        if not wd.exists():
            return
        try:
            shutil.rmtree(wd)
        except Exception:
            log.exception("orchestrator.cleanup_error", work_dir=str(wd))
