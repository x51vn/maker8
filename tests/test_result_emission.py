"""Tests for XST-1054: unified result topic/key resolution helpers in emit.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from maker8.models.common import JobStatus, RenderStage
from maker8.models.contracts import ResultDestination
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.emit import EmitResultStage, resolve_result_key, resolve_result_topic
from maker8.retry import StageError
from render_contracts.render_spec import RenderSpec


def _make_ctx(
    *,
    job_id: str = "job-abc",
    result_destination: ResultDestination | None = None,
) -> PipelineContext:
    return PipelineContext(
        job_id=job_id,
        render_spec=RenderSpec(),
        result_destination=result_destination,
    )


class TestResolveResultTopic:
    def test_uses_destination_topic_when_set(self) -> None:
        ctx = _make_ctx(result_destination=ResultDestination(topic="custom.topic.v1"))
        assert resolve_result_topic(ctx, "default.topic") == "custom.topic.v1"

    def test_uses_default_when_destination_is_none(self) -> None:
        ctx = _make_ctx(result_destination=None)
        assert resolve_result_topic(ctx, "fallback.topic") == "fallback.topic"

    def test_uses_default_when_destination_topic_is_empty(self) -> None:
        ctx = _make_ctx(result_destination=ResultDestination(topic=""))
        assert resolve_result_topic(ctx, "fallback.topic") == "fallback.topic"


class TestResolveResultKey:
    def test_uses_destination_key_when_set(self) -> None:
        ctx = _make_ctx(
            job_id="job-xyz",
            result_destination=ResultDestination(key="custom-key"),
        )
        assert resolve_result_key(ctx) == "custom-key"

    def test_uses_job_id_when_destination_is_none(self) -> None:
        ctx = _make_ctx(job_id="job-xyz", result_destination=None)
        assert resolve_result_key(ctx) == "job-xyz"

    def test_uses_job_id_when_destination_key_is_empty(self) -> None:
        ctx = _make_ctx(
            job_id="job-xyz",
            result_destination=ResultDestination(key=""),
        )
        assert resolve_result_key(ctx) == "job-xyz"


class TestResultEmittedMetricLabel:
    """Verify RESULT_EMITTED is labelled correctly for success and failure paths."""

    def test_emit_stage_done_metric(self) -> None:
        mock_producer = MagicMock()
        stage = EmitResultStage(producer=mock_producer, result_topic="result.topic")
        ctx = _make_ctx()

        with patch("maker8.pipeline.emit.RESULT_EMITTED") as mock_metric:
            stage.execute(ctx)

        mock_metric.labels.assert_called_once_with(status=JobStatus.DONE.value)
        mock_metric.labels.return_value.inc.assert_called_once()

    def test_emit_stage_partial_metric(self) -> None:
        mock_producer = MagicMock()
        stage = EmitResultStage(producer=mock_producer, result_topic="result.topic")
        ctx = _make_ctx()
        # Inject a warning so the job is marked as degraded
        from maker8.models.common import AssetWarning

        ctx.warnings.append(AssetWarning(code="W", message="degraded"))

        with patch("maker8.pipeline.emit.RESULT_EMITTED") as mock_metric:
            stage.execute(ctx)

        mock_metric.labels.assert_called_once_with(status=JobStatus.PARTIAL.value)

    def test_orchestrator_failed_result_metric(self) -> None:
        """orchestrator._send_failed_result uses RESULT_EMITTED with status='FAILED'."""
        from maker8.config import Settings
        from maker8.pipeline.orchestrator import Orchestrator

        mock_producer = MagicMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.kafka_render_result_topic = "result.topic"

        orch = Orchestrator.__new__(Orchestrator)
        orch._producer = mock_producer
        orch._settings = mock_settings

        ctx = _make_ctx()
        exc = StageError(RenderStage.RENDER, "RENDER_FAILED", "oops", retryable=False)

        with patch("maker8.pipeline.orchestrator.RESULT_EMITTED") as mock_metric:
            orch._send_failed_result(ctx, exc)

        mock_metric.labels.assert_called_once_with(status="FAILED")
        mock_metric.labels.return_value.inc.assert_called_once()
