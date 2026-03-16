"""EMIT_RESULT stage – produce the RenderResult to Kafka."""

from __future__ import annotations

from maker8.kafka.producer import KafkaProducer
from maker8.models.common import JobStatus, RenderStage
from maker8.models.contracts import DropboxOutput, RenderResult
from maker8.observability.metrics import RESULT_EMITTED
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.retry import StageError
from maker8.utils.logging import get_logger
from maker8.utils.versions import collect_engine_versions

log = get_logger(__name__)


class EmitResultStage(Stage):
    def __init__(self, producer: KafkaProducer, result_topic: str) -> None:
        self._producer = producer
        self._topic = result_topic

    @property
    def name(self) -> RenderStage:
        return RenderStage.EMIT_RESULT

    def execute(self, ctx: PipelineContext) -> None:
        status = JobStatus.PARTIAL if ctx.is_degraded else JobStatus.DONE
        log.info(
            "emit.start",
            job_id=ctx.job_id,
            topic=self._topic,
            status=status.value,
            degraded=ctx.is_degraded,
        )
        try:
            result = self._build_result(ctx)
            payload = result.model_dump(mode="json", by_alias=True)
            self._producer.send(self._topic, key=ctx.job_id, value=payload)
            RESULT_EMITTED.labels(status=status.value).inc()
            log.info(
                "emit.success",
                job_id=ctx.job_id,
                topic=self._topic,
                status=status.value,
            )
        except Exception as exc:
            log.error(
                "emit.failure",
                job_id=ctx.job_id,
                topic=self._topic,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise StageError(
                self.name, "EMIT_FAILED",
                f"Failed to produce render result: {exc}",
            ) from exc

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_result(ctx: PipelineContext) -> RenderResult:
        dropbox = DropboxOutput()
        if ctx.dropbox_video_ref:
            dropbox.video = ctx.dropbox_video_ref
        if ctx.dropbox_manifest_ref:
            dropbox.manifest = ctx.dropbox_manifest_ref

        status = JobStatus.PARTIAL if ctx.is_degraded else JobStatus.DONE

        return RenderResult(
            job_id=ctx.job_id,
            status=status,
            job_key=ctx.job_key,
            dry_run=ctx.dry_run,
            canvas_profile=ctx.canvas_profile,
            dropbox=dropbox,
            output_meta=ctx.output_meta,
            uploader_metadata=ctx.uploader_metadata,
            publish_targets=ctx.render_spec.publish.targets,
            asset_report=ctx.asset_report,
            warnings=ctx.warnings,
            engine_versions=collect_engine_versions(),
            trace=ctx.trace,
        )
