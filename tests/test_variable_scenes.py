"""Tests for variable scene count support – XST-1036.

Verifies that maker8 pipeline stages handle 1–15 scenes without
any hardcoded assumptions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maker8.models.spec import RenderSpec
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.validate import ValidateStage


def _build_spec(scene_count: int) -> dict:
    """Build a minimal RenderSpec dict with *scene_count* scenes."""
    assets = [
        {
            "id": f"a{i}",
            "type": "video",
            "source": {"kind": "http", "url": f"https://example.com/{i}.mp4"},
        }
        for i in range(1, scene_count + 1)
    ]
    scenes = [
        {
            "scene_id": f"scene_{i:02d}",
            "narration": {"text": f"Scene {i} narration."},
            "layers": [
                {
                    "layer_id": f"l{i}",
                    "type": "video",
                    "asset_ref": f"a{i}",
                    "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920},
                },
            ],
        }
        for i in range(1, scene_count + 1)
    ]
    return {
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
        "assets": assets,
        "scenes": scenes,
    }


def _make_ctx(tmp_path: Path, scene_count: int) -> PipelineContext:
    from maker8.models.common import Trace

    spec = RenderSpec.model_validate(_build_spec(scene_count))
    return PipelineContext(
        job_id="test-variable-scenes",
        render_spec=spec,
        trace=Trace(),
        work_dir=tmp_path,
        assets_dir=tmp_path / "assets",
        tts_dir=tmp_path / "tts",
        output_dir=tmp_path / "output",
    )


class TestVariableSceneCountParsing:
    """RenderSpec parses correctly with variable scene counts."""

    @pytest.mark.parametrize("n", [1, 2, 3, 5, 7, 10, 15])
    def test_parse_n_scenes(self, n: int) -> None:
        spec = RenderSpec.model_validate(_build_spec(n))
        assert len(spec.scenes) == n
        assert len(spec.assets) == n

    def test_single_scene_has_correct_id(self) -> None:
        spec = RenderSpec.model_validate(_build_spec(1))
        assert spec.scenes[0].scene_id == "scene_01"


class TestValidateStageVariableScenes:
    """ValidateStage accepts variable scene counts."""

    @pytest.mark.parametrize("n", [1, 3, 5, 7, 10, 15])
    def test_validate_n_scenes(self, tmp_path: Path, n: int) -> None:
        ctx = _make_ctx(tmp_path, n)
        stage = ValidateStage()
        stage.execute(ctx)
        assert len(ctx.render_spec.scenes) == n

    def test_validate_zero_scenes_fails(self, tmp_path: Path) -> None:
        from maker8.retry import StageError

        ctx = _make_ctx(tmp_path, 1)
        ctx.render_spec.scenes = []
        stage = ValidateStage()
        with pytest.raises(StageError, match="at least one scene"):
            stage.execute(ctx)
