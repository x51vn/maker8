"""Tests for missing-asset policy deduplication (XST-1053).

Verifies that when a required layer has a missing asset, exactly ONE warning
is emitted – the MISSING_ASSET_POLICY_APPLIED from the RENDER stage – not
both that and a LAYER_ASSET_MISSING from the composer.

Non-required layers should still produce their LAYER_ASSET_MISSING warning
(emitted by the composer only).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maker8.models.common import AssetWarning, JobStatus, OutputMeta, RenderStage
from maker8.models.spec import (
    Asset,
    Canvas,
    Defaults,
    Layer,
    NarrationDefaults,
    Rect,
    RenderSpec,
    Scene,
    SceneNarration,
    SceneTiming,
)
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.render import RenderStageImpl
from maker8.plugins.registry import PluginRegistry
from maker8.rendering.composer import RenderInput
from maker8.retry import StageError


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_spec_with_layer(
    asset_id: str = "a1",
    layer_required: bool = True,
    missing_asset_policy: str = "drop_layer",
    layer_type: str = "video",
) -> RenderSpec:
    return RenderSpec.model_validate(
        {
            "spec_version": "1.0",
            "canvas": {"w": 640, "h": 480, "fps": 24, "bg": "#000000"},
            "defaults": {
                "narration": {"lang": "vi-VN", "tts_preset_ref": "tts:vi:default"},
                "scene_timing": {
                    "head_pad_sec": 0.0,
                    "tail_pad_sec": 0.0,
                    "duration_mode": "auto_from_tts",
                },
            },
            "assets": [
                {"id": asset_id, "type": layer_type, "source": {"kind": "http", "url": "http://x.com/v.mp4"}}
            ],
            "scenes": [
                {
                    "scene_id": "s1",
                    "narration": {"text": "Hello."},
                    "layers": [
                        # Text layer keeps the scene viable even when the video asset is missing
                        {
                            "layer_id": "l0",
                            "type": "text",
                            "rect": {"x": 0, "y": 0, "w": 640, "h": 100},
                            "text_content": "Title",
                            "required": False,
                        },
                        {
                            "layer_id": "l1",
                            "type": layer_type,
                            "asset_ref": asset_id,
                            "rect": {"x": 0, "y": 0, "w": 640, "h": 480},
                            "required": layer_required,
                            "missing_asset_policy": missing_asset_policy,
                        },
                    ],
                }
            ],
        }
    )


def _make_ctx(tmp_path: Path, spec: RenderSpec) -> PipelineContext:
    ctx = PipelineContext(
        job_id="test-job",
        render_spec=spec,
        work_dir=tmp_path,
        assets_dir=tmp_path / "assets",
        tts_dir=tmp_path / "tts",
        output_dir=tmp_path / "output",
    )
    ctx.ensure_dirs()
    # Simulate TTS result for the scene (empty tuple would fail; provide a mock)
    dummy_audio = tmp_path / "dummy.mp3"
    dummy_audio.write_bytes(b"")
    ctx.tts_results["s1"] = MagicMock(audio_path=dummy_audio, duration_sec=1.0)
    return ctx


def _make_stage() -> RenderStageImpl:
    registry = MagicMock(spec=PluginRegistry)
    registry.get_effect.side_effect = KeyError("no effect")
    return RenderStageImpl(registry=registry)


def _stub_compose_video(warnings: list[AssetWarning] | None = None) -> MagicMock:
    """Return a mock compose_video that appends *warnings* to ri.warnings."""
    fake_video = MagicMock()
    fake_meta = OutputMeta()

    def _compose(ri: RenderInput) -> tuple[object, OutputMeta]:
        if warnings:
            ri.warnings.extend(warnings)
        return fake_video, fake_meta

    return MagicMock(side_effect=_compose)


# ── Tests ────────────────────────────────────────────────────────────────────


class TestMissingAssetSingleWarning:
    def test_required_drop_layer_single_warning(self, tmp_path: Path) -> None:
        """Required layer with drop_layer policy → exactly 1 MISSING_ASSET_POLICY_APPLIED."""
        spec = _make_spec_with_layer(
            layer_required=True,
            missing_asset_policy="drop_layer",
        )
        ctx = _make_ctx(tmp_path, spec)
        # asset_paths is empty → asset IS missing

        stage = _make_stage()
        with patch("maker8.pipeline.render.compose_video", _stub_compose_video()):
            stage.execute(ctx)

        policy_warnings = [
            w for w in ctx.warnings if w.code == "MISSING_ASSET_POLICY_APPLIED"
        ]
        duplicate_warnings = [
            w for w in ctx.warnings if w.code == "LAYER_ASSET_MISSING"
        ]
        assert len(policy_warnings) == 1, f"Expected 1 MISSING_ASSET_POLICY_APPLIED, got {policy_warnings}"
        assert len(duplicate_warnings) == 0, f"Unexpected LAYER_ASSET_MISSING warnings: {duplicate_warnings}"

    def test_required_placeholder_single_warning(self, tmp_path: Path) -> None:
        """Required layer with scene_placeholder policy → exactly 1 warning total."""
        spec = _make_spec_with_layer(
            layer_required=True,
            missing_asset_policy="scene_placeholder",
        )
        ctx = _make_ctx(tmp_path, spec)

        stage = _make_stage()
        with patch("maker8.pipeline.render.compose_video", _stub_compose_video()):
            stage.execute(ctx)

        total_warnings = [w for w in ctx.warnings if w.asset_id == "a1"]
        assert len(total_warnings) == 1, (
            f"Expected exactly 1 warning for a1, got {total_warnings}"
        )
        assert total_warnings[0].code == "MISSING_ASSET_POLICY_APPLIED"

    def test_non_required_layer_warning_from_composer(self, tmp_path: Path) -> None:
        """Non-required layer → no stage warning; composer warning IS emitted."""
        spec = _make_spec_with_layer(
            layer_required=False,
            missing_asset_policy="drop_layer",
        )
        ctx = _make_ctx(tmp_path, spec)

        composer_warning = AssetWarning(
            asset_id="a1",
            scene_id="s1",
            stage="RENDER",
            code="LAYER_ASSET_MISSING",
            message="missing",
            fallback_used="drop_layer",
        )
        stage = _make_stage()
        with patch(
            "maker8.pipeline.render.compose_video",
            _stub_compose_video(warnings=[composer_warning]),
        ):
            stage.execute(ctx)

        stage_warnings = [
            w for w in ctx.warnings if w.code == "MISSING_ASSET_POLICY_APPLIED"
        ]
        composer_warnings = [
            w for w in ctx.warnings if w.code == "LAYER_ASSET_MISSING"
        ]
        assert len(stage_warnings) == 0, "Stage should not warn about non-required layers"
        assert len(composer_warnings) == 1, "Composer should emit one LAYER_ASSET_MISSING for non-required layer"

    def test_fail_request_raises_stage_error(self, tmp_path: Path) -> None:
        """Required layer with fail_request policy → StageError, not a warning."""
        spec = _make_spec_with_layer(
            layer_required=True,
            missing_asset_policy="fail_request",
        )
        ctx = _make_ctx(tmp_path, spec)

        stage = _make_stage()
        with patch("maker8.pipeline.render.compose_video", _stub_compose_video()):
            with pytest.raises(StageError) as exc_info:
                stage.execute(ctx)

        assert exc_info.value.code == "REQUIRED_ASSET_MISSING"
        # A MISSING_ASSET_POLICY_APPLIED warning is added before the raise
        policy_warnings = [
            w for w in ctx.warnings if w.code == "MISSING_ASSET_POLICY_APPLIED"
        ]
        assert len(policy_warnings) == 1
