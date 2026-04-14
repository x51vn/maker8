"""Tests for ValidateStage spec_version mismatch enforcement (XST-1052).

Verifies that the VALIDATE stage raises SPEC_VERSION_MISMATCH when
request.spec_version != render_spec.spec_version.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maker8.models.contracts import RenderRequest
from maker8.models.spec import (
    RenderSpec,
)
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.validate import ValidateStage
from maker8.retry import StageError

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_minimal_spec(spec_version: str = "1.0") -> RenderSpec:
    """Return the smallest valid RenderSpec at the given spec_version."""
    layer: dict = {
        "layer_id": "l1",
        "type": "video",
        "asset_ref": "a1",
        "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920},
    }
    if spec_version == "2.0":
        layer["role"] = "primary_visual"
        layer["required"] = True
    return RenderSpec.model_validate(
        {
            "spec_version": spec_version,
            "canvas": {"w": 1080, "h": 1920, "fps": 30, "bg": "#000000"},
            "defaults": {
                "narration": {"lang": "vi-VN", "tts_preset_ref": "tts:vi:default"},
                "scene_timing": {
                    "head_pad_sec": 0.15,
                    "tail_pad_sec": 0.45,
                    "duration_mode": "auto_from_tts",
                },
            },
            "assets": [
                {
                    "id": "a1",
                    "type": "video",
                    "source": {"kind": "http", "url": "https://x.com/1.mp4"},
                }
            ],
            "scenes": [
                {
                    "scene_id": "s1",
                    "narration": {"text": "Hello world."},
                    "layers": [layer],
                }
            ],
        }
    )


def _make_request(
    request_version: str,
    spec_version: str,
) -> RenderRequest:
    """Return a RenderRequest with potentially mismatched spec_version fields."""
    spec = _make_minimal_spec(spec_version=spec_version)
    return RenderRequest.model_validate(
        {
            "job_id": "test-job",
            "spec_version": request_version,
            "render_spec": spec.model_dump(mode="json"),
        }
    )


def _make_ctx(tmp_path: Path, request: RenderRequest) -> PipelineContext:
    return PipelineContext(
        job_id=request.job_id,
        render_spec=request.render_spec,
        request=request,
        work_dir=tmp_path,
        assets_dir=tmp_path / "assets",
        tts_dir=tmp_path / "tts",
        output_dir=tmp_path / "output",
    )


# ── Tests ────────────────────────────────────────────────────────────────────


class TestSpecVersionMismatch:
    def test_mismatch_raises_stage_error(self, tmp_path: Path) -> None:
        """request.spec_version=2.0 but render_spec.spec_version=1.0 → SPEC_VERSION_MISMATCH."""
        req = _make_request(request_version="2.0", spec_version="1.0")
        ctx = _make_ctx(tmp_path, req)
        stage = ValidateStage()

        with pytest.raises(StageError) as exc_info:
            stage.execute(ctx)

        err = exc_info.value
        assert err.code == "SPEC_VERSION_MISMATCH"
        assert "2.0" in str(err)
        assert "1.0" in str(err)

    def test_mismatch_reversed_raises_stage_error(self, tmp_path: Path) -> None:
        """request.spec_version=1.0 but render_spec.spec_version=2.0 → SPEC_VERSION_MISMATCH."""
        req = _make_request(request_version="1.0", spec_version="2.0")
        ctx = _make_ctx(tmp_path, req)
        stage = ValidateStage()

        with pytest.raises(StageError) as exc_info:
            stage.execute(ctx)

        assert exc_info.value.code == "SPEC_VERSION_MISMATCH"

    def test_mismatch_is_non_retryable(self, tmp_path: Path) -> None:
        """SPEC_VERSION_MISMATCH errors must be non-retryable."""
        req = _make_request(request_version="2.0", spec_version="1.0")
        ctx = _make_ctx(tmp_path, req)

        with pytest.raises(StageError) as exc_info:
            ValidateStage().execute(ctx)

        assert exc_info.value.retryable is False

    def test_match_v1_passes(self, tmp_path: Path) -> None:
        """Both spec_version=1.0 must pass validation."""
        req = _make_request(request_version="1.0", spec_version="1.0")
        ctx = _make_ctx(tmp_path, req)
        # Should not raise
        ValidateStage().execute(ctx)

    def test_match_v2_passes(self, tmp_path: Path) -> None:
        """Both spec_version=2.0 must pass validation."""
        req = _make_request(request_version="2.0", spec_version="2.0")
        ctx = _make_ctx(tmp_path, req)
        # Should not raise
        ValidateStage().execute(ctx)

    def test_no_request_in_ctx_skips_check(self, tmp_path: Path) -> None:
        """ctx.request=None (backward compat) must not cause a SPEC_VERSION_MISMATCH error."""
        spec = _make_minimal_spec(spec_version="1.0")
        ctx = PipelineContext(
            job_id="job-no-req",
            render_spec=spec,
            request=None,  # no envelope
            work_dir=tmp_path,
            assets_dir=tmp_path / "assets",
            tts_dir=tmp_path / "tts",
            output_dir=tmp_path / "output",
        )
        # Should not raise
        ValidateStage().execute(ctx)
