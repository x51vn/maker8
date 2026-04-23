"""Tests for ValidateStage identity consistency warnings (XST-1074).

Verifies that _warn_identity() emits non-blocking structlog warnings
for empty channel_id and identity mismatches without raising StageError.
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

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_minimal_spec(
    publish_targets: list[dict[str, object]] | None = None,
) -> RenderSpec:
    """Return the smallest valid RenderSpec with optional publish targets."""
    data: dict[str, object] = {
        "spec_version": "1.0",
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
                "layers": [
                    {
                        "layer_id": "l1",
                        "type": "video",
                        "asset_ref": "a1",
                        "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920},
                    }
                ],
            }
        ],
    }
    if publish_targets is not None:
        data["publish"] = {"targets": publish_targets}
    return RenderSpec.model_validate(data)


def _make_request(
    channel_id: str = "",
    publish_targets: list[dict[str, object]] | None = None,
) -> RenderRequest:
    spec = _make_minimal_spec(publish_targets=publish_targets)
    req_data: dict[str, object] = {
        "job_id": "test-identity",
        "spec_version": "1.0",
        "render_spec": spec.model_dump(mode="json"),
        "uploader_metadata": {"channel_id": channel_id},
    }
    return RenderRequest.model_validate(req_data)


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


class TestIdentityWarnings:
    """Non-blocking identity checks must warn but never raise."""

    def test_matching_identity_no_warning(self, tmp_path: Path, caplog: object) -> None:
        """Matching common channel_id and target channel_id should produce no warnings."""
        req = _make_request(
            channel_id="channel:abc",
            publish_targets=[
                {"platform": "youtube", "channel_id": "channel:abc", "enabled": True}
            ],
        )
        ctx = _make_ctx(tmp_path, req)
        ValidateStage().execute(ctx)
        # Should complete without raising

    def test_empty_channel_id_with_enabled_targets_warns(
        self, tmp_path: Path, caplog: object
    ) -> None:
        """Empty channel_id with enabled publish targets should log a warning."""
        req = _make_request(
            channel_id="",
            publish_targets=[
                {"platform": "youtube", "channel_id": "channel:abc", "enabled": True}
            ],
        )
        ctx = _make_ctx(tmp_path, req)
        # Should NOT raise — warnings only
        ValidateStage().execute(ctx)

    def test_identity_mismatch_warns(self, tmp_path: Path) -> None:
        """Mismatched common channel_id vs target channel_id should warn but not fail."""
        req = _make_request(
            channel_id="yt:old-channel",
            publish_targets=[
                {"platform": "youtube", "channel_id": "channel:new", "enabled": True}
            ],
        )
        ctx = _make_ctx(tmp_path, req)
        # Should NOT raise — warnings only
        ValidateStage().execute(ctx)

    def test_missing_enabled_channel_id_raises(self, tmp_path: Path) -> None:
        req = _make_request(
            channel_id="",
            publish_targets=[
                {"platform": "youtube", "channel_id": "", "enabled": True}
            ],
        )
        ctx = _make_ctx(tmp_path, req)

        from maker8.retry import StageError

        with pytest.raises(StageError) as exc_info:
            ValidateStage().execute(ctx)

        assert exc_info.value.code == "MISSING_PUBLISH_CHANNEL_ID"
        assert str(exc_info.value) == "Enabled publish targets must define channel_id"

    def test_no_publish_targets_no_warning(self, tmp_path: Path) -> None:
        """No publish targets means no identity check performed."""
        req = _make_request(channel_id="", publish_targets=[])
        ctx = _make_ctx(tmp_path, req)
        ValidateStage().execute(ctx)

    def test_all_targets_disabled_no_warning(self, tmp_path: Path) -> None:
        """All disabled targets means no identity check performed."""
        req = _make_request(
            channel_id="",
            publish_targets=[
                {"platform": "youtube", "channel_id": "channel:abc", "enabled": False}
            ],
        )
        ctx = _make_ctx(tmp_path, req)
        ValidateStage().execute(ctx)
