"""Tests for pipeline retry correctness and render_max_attempts validation.

Covers:
- Transient OSError is retried up to render_max_attempts times.
- Non-retryable StageError is never retried.
- Retryable StageError is retried up to render_max_attempts times.
- render_max_attempts=0 / negative is rejected at Settings construction.
- PipelineContext.from_request() ValueError is caught and emits DLQ.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from maker8.models.common import RenderStage
from maker8.pipeline.orchestrator import Orchestrator
from maker8.retry import RetryPolicy, StageError


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_orchestrator(max_attempts: int = 3) -> Orchestrator:
    """Build a minimal Orchestrator with a configurable retry policy."""
    orch = object.__new__(Orchestrator)
    orch._settings = MagicMock()
    orch._settings.work_dir = Path("/tmp/maker8_test")
    orch._settings.kafka_render_dlq_topic = "video.render.dlq.v1"
    orch._producer = MagicMock()
    orch._state = None
    orch._retry_policy = RetryPolicy(
        max_attempts=max_attempts,
        min_delay_sec=0.0,  # no real sleeping in tests
        max_delay_sec=0.0,
    )
    orch._stages = []
    return orch


class _SpyStage:
    """A test stage that raises a given exception N times then succeeds."""

    def __init__(
        self,
        name: RenderStage,
        fail_times: int = 0,
        exc: Exception | None = None,
    ) -> None:
        self._name = name
        self.fail_times = fail_times
        self.exc = exc or StageError(name, "ERR", "fail", retryable=True)
        self.call_count = 0

    @property
    def name(self) -> RenderStage:
        return self._name

    def execute(self, ctx: object) -> None:
        self.call_count += 1
        if self.call_count <= self.fail_times:
            raise self.exc


def _make_ctx(tmp_path: Path) -> object:
    from maker8.models.common import Trace
    from maker8.pipeline.context import PipelineContext
    from maker8.models.spec import RenderSpec

    spec = RenderSpec.model_validate({
        "canvas": {"w": 1080, "h": 1920, "fps": 30, "bg": "#000000"},
        "assets": [
            {"id": "a1", "type": "video", "source": {"kind": "http", "url": "https://x.com/1.mp4"}},
        ],
        "scenes": [
            {
                "scene_id": "s1",
                "narration": {"text": "Hello."},
                "layers": [
                    {"layer_id": "l1", "type": "video", "asset_ref": "a1",
                     "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920}},
                ],
            }
        ],
    })
    return PipelineContext(
        job_id="test-retry",
        render_spec=spec,
        trace=Trace(),
        work_dir=tmp_path,
        assets_dir=tmp_path / "assets",
        tts_dir=tmp_path / "tts",
        output_dir=tmp_path / "output",
    )


# ── retry logic tests ─────────────────────────────────────────────────────────


class TestRetryRouting:
    def test_transient_oserror_is_retried(self, tmp_path: Path) -> None:
        """An OSError raised by a stage must be wrapped as retryable and retried."""
        orch = _make_orchestrator(max_attempts=3)
        ctx = _make_ctx(tmp_path)

        stage = _SpyStage(
            RenderStage.DOWNLOAD,
            fail_times=2,
            exc=OSError("connection reset"),
        )

        with patch("time.sleep"):  # avoid real delays
            orch._execute_with_retry(stage, ctx)

        # Must have been called 3 times: 2 failures + 1 success
        assert stage.call_count == 3

    def test_oserror_exhausted_raises_stage_error(self, tmp_path: Path) -> None:
        """An OSError that persists beyond max_attempts must raise StageError."""
        orch = _make_orchestrator(max_attempts=2)
        ctx = _make_ctx(tmp_path)

        stage = _SpyStage(
            RenderStage.DOWNLOAD,
            fail_times=999,
            exc=OSError("connection reset"),
        )

        with patch("time.sleep"):
            with pytest.raises(StageError) as exc_info:
                orch._execute_with_retry(stage, ctx)

        assert exc_info.value.code == "UNEXPECTED_ERROR"
        assert exc_info.value.retryable is True
        assert stage.call_count == 2

    def test_non_retryable_stage_error_not_retried(self, tmp_path: Path) -> None:
        """A non-retryable StageError must be raised on the first attempt."""
        orch = _make_orchestrator(max_attempts=5)
        ctx = _make_ctx(tmp_path)

        exc = StageError(RenderStage.VALIDATE, "INVALID", "bad spec", retryable=False)
        stage = _SpyStage(RenderStage.VALIDATE, fail_times=999, exc=exc)

        with patch("time.sleep"):
            with pytest.raises(StageError) as exc_info:
                orch._execute_with_retry(stage, ctx)

        assert exc_info.value is exc
        assert stage.call_count == 1  # not retried

    def test_retryable_stage_error_retried_then_succeeds(self, tmp_path: Path) -> None:
        """A retryable StageError must be retried and succeed when it recovers."""
        orch = _make_orchestrator(max_attempts=3)
        ctx = _make_ctx(tmp_path)

        exc = StageError(RenderStage.TTS, "TTS_FAIL", "provider down", retryable=True)
        stage = _SpyStage(RenderStage.TTS, fail_times=2, exc=exc)

        with patch("time.sleep"):
            orch._execute_with_retry(stage, ctx)  # must not raise

        assert stage.call_count == 3  # 2 failures + 1 success

    def test_unknown_exception_wrapped_non_oserror(self, tmp_path: Path) -> None:
        """A generic ValueError from a stage is wrapped as non-retryable StageError."""
        orch = _make_orchestrator(max_attempts=3)
        ctx = _make_ctx(tmp_path)

        exc = ValueError("unexpected codec enum")
        stage = _SpyStage(RenderStage.NORMALIZE, fail_times=999, exc=exc)

        with patch("time.sleep"):
            with pytest.raises(StageError) as exc_info:
                orch._execute_with_retry(stage, ctx)

        wrapped = exc_info.value
        assert wrapped.code == "UNEXPECTED_ERROR"
        assert wrapped.retryable is False  # ValueError is not OSError
        assert wrapped.__cause__ is exc
        assert stage.call_count == 1  # not retried because not retryable


# ── settings validation ───────────────────────────────────────────────────────


class TestRenderMaxAttemptsValidation:
    def test_zero_render_max_attempts_rejected(self) -> None:
        """render_max_attempts=0 must raise ValidationError at construction."""
        import os

        env = {
            "MAKER8_RENDER_MAX_ATTEMPTS": "0",
            # Provide required-ish settings so only the target field fails
            "MAKER8_EDITOR8_DATABASE_URL": "postgresql://x/y",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValidationError) as exc_info:
                from maker8.config import Settings

                Settings()

        errors = exc_info.value.errors()
        fields = [e["loc"][-1] for e in errors]
        assert "render_max_attempts" in fields

    def test_negative_render_max_attempts_rejected(self) -> None:
        """render_max_attempts=-1 must raise ValidationError at construction."""
        import os

        env = {"MAKER8_RENDER_MAX_ATTEMPTS": "-1"}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValidationError) as exc_info:
                from maker8.config import Settings

                Settings()

        errors = exc_info.value.errors()
        fields = [e["loc"][-1] for e in errors]
        assert "render_max_attempts" in fields

    def test_positive_render_max_attempts_accepted(self) -> None:
        """render_max_attempts=1 must be accepted."""
        import os

        env = {"MAKER8_RENDER_MAX_ATTEMPTS": "1"}
        with patch.dict(os.environ, env, clear=False):
            from maker8.config import Settings

            s = Settings()
        assert s.render_max_attempts == 1


# ── PipelineContext.from_request ValueError caught by orchestrator ─────────────


class TestOrchestratorContextCreation:
    def test_invalid_job_id_emits_dlq_not_raises(self) -> None:
        """An invalid job_id in an otherwise-valid RenderRequest must be caught
        and emitted to DLQ rather than propagating to the consumer."""
        import json

        orch = object.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.work_dir = Path("/tmp/maker8_test")
        orch._settings.kafka_render_dlq_topic = "video.render.dlq.v1"
        orch._producer = MagicMock()
        orch._state = None
        orch._retry_policy = RetryPolicy(
            max_attempts=1,
            min_delay_sec=0.0,
            max_delay_sec=0.0,
        )
        orch._stages = []

        # A valid RenderRequest payload except job_id contains path-traversal chars
        payload = {
            "job_id": "../etc/passwd",  # invalid – contains '/'
            "render_spec": {
                "assets": [
                    {"id": "a1", "type": "video",
                     "source": {"kind": "http", "url": "https://x.com/1.mp4"}},
                ],
                "scenes": [
                    {"scene_id": "s1", "narration": {"text": "Hi."},
                     "layers": []},
                ],
            },
        }

        # Should not raise; should return and attempt DLQ emit
        orch.handle(payload)

        # DLQ producer must have been called
        orch._producer.send.assert_called_once()
        call_args = orch._producer.send.call_args
        topic = call_args[0][0]
        assert topic == "video.render.dlq.v1"
