"""V2 contract tests – spec_version 2.0 validation, roles, subtitles, policies.

Covers PRD §N-4: variable scene counts, subtitle on/off, subtitle override,
missing primary_visual with each policy, and intentional text-only scenes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maker8.models.common import Trace
from maker8.models.contracts import RenderRequest
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.validate import ValidateStage
from maker8.retry import StageError

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _v2_spec(
    scene_count: int = 3,
    *,
    subtitles_enabled: bool = False,
    roles: bool = True,
    missing_asset_policy: str = "scene_placeholder",
) -> dict:
    """Build a minimal V2 RenderSpec dict."""
    assets = [
        {
            "id": f"a{i}",
            "type": "image",
            "source": {"kind": "http", "url": f"https://example.com/{i}.jpg"},
        }
        for i in range(1, scene_count + 1)
    ]
    scenes = []
    for i in range(1, scene_count + 1):
        layer: dict = {
            "layer_id": f"l{i}",
            "type": "image",
            "asset_ref": f"a{i}",
            "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920},
        }
        if roles:
            layer["role"] = "primary_visual"
            layer["required"] = True
            layer["missing_asset_policy"] = missing_asset_policy
        scenes.append(
            {
                "scene_id": f"scene_{i:02d}",
                "narration": {"text": f"Scene {i} narration."},
                "layers": [layer],
            }
        )
    return {
        "spec_version": "2.0",
        "canvas": {"w": 1080, "h": 1920, "fps": 30, "bg": "#000000"},
        "defaults": {
            "narration": {"lang": "vi-VN", "tts_preset_ref": "tts:vi:default"},
            "scene_timing": {"head_pad_sec": 0.15, "tail_pad_sec": 0.45},
            "subtitles": {
                "enabled": subtitles_enabled,
                "source": "narration",
                "max_lines": 2,
            },
        },
        "assets": assets,
        "scenes": scenes,
    }


def _v2_request(
    scene_count: int = 3,
    *,
    subtitles_enabled: bool = False,
    roles: bool = True,
    missing_asset_policy: str = "scene_placeholder",
    planning_count: int | None = None,
) -> dict:
    """Build a V2 RenderRequest dict."""
    spec = _v2_spec(
        scene_count,
        subtitles_enabled=subtitles_enabled,
        roles=roles,
        missing_asset_policy=missing_asset_policy,
    )
    req: dict = {
        "job_id": "test-v2",
        "spec_version": "2.0",
        "render_spec": spec,
    }
    if planning_count is not None:
        req["planning"] = {
            "target_duration_sec": 60.0,
            "duration_source": "user_input",
            "scene_count_policy_version": "v2.0",
            "planned_scene_count": planning_count,
        }
    return req


def _make_ctx(tmp_path: Path, req_dict: dict) -> PipelineContext:
    """Build a PipelineContext from a raw request dict."""
    request = RenderRequest.model_validate(req_dict)
    return PipelineContext(
        job_id=request.job_id,
        render_spec=request.render_spec,
        trace=Trace(),
        request=request,
        work_dir=tmp_path,
        assets_dir=tmp_path / "assets",
        tts_dir=tmp_path / "tts",
        output_dir=tmp_path / "output",
    )


# ── Golden V2 fixture ────────────────────────────────────────────────────────


class TestGoldenV2Fixture:
    """Verify golden_v2_request.json parses and round-trips."""

    def test_parse(self) -> None:
        payload = json.loads((FIXTURES_DIR / "golden_v2_request.json").read_text())
        req = RenderRequest.model_validate(payload)
        assert req.spec_version == "2.0"
        assert req.render_spec.spec_version == "2.0"
        assert req.planning is not None
        assert req.planning.planned_scene_count == 3

    def test_round_trip(self) -> None:
        payload = json.loads((FIXTURES_DIR / "golden_v2_request.json").read_text())
        req = RenderRequest.model_validate(payload)
        dumped = req.model_dump(mode="json", by_alias=True)
        req2 = RenderRequest.model_validate(dumped)
        assert req == req2

    def test_v2_layer_roles_present(self) -> None:
        payload = json.loads((FIXTURES_DIR / "golden_v2_request.json").read_text())
        req = RenderRequest.model_validate(payload)
        scene1 = req.render_spec.scenes[0]
        bg = scene1.layers[0]
        assert bg.role == "primary_visual"
        assert bg.required is True
        assert bg.missing_asset_policy == "scene_placeholder"

    def test_v2_subtitle_override(self) -> None:
        payload = json.loads((FIXTURES_DIR / "golden_v2_request.json").read_text())
        req = RenderRequest.model_validate(payload)
        scene3 = req.render_spec.scenes[2]
        assert scene3.subtitle is not None
        assert scene3.subtitle.enabled is True
        assert scene3.subtitle.source == "custom"
        assert scene3.subtitle.text == "Custom subtitle text for scene 3."


# ── Variable scene counts ────────────────────────────────────────────────────


class TestV2VariableSceneCounts:
    """V2 requests with 1, 3, 7, 10+ scenes validate successfully."""

    @pytest.mark.parametrize("n", [1, 3, 7, 10])
    def test_v2_n_scenes(self, tmp_path: Path, n: int) -> None:
        req_dict = _v2_request(n, planning_count=n)
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)
        assert len(ctx.render_spec.scenes) == n


# ── Subtitle on/off ──────────────────────────────────────────────────────────


class TestV2SubtitleToggle:
    """Validate subtitle defaults and scene overrides."""

    def test_subtitle_off_by_default(self, tmp_path: Path) -> None:
        req_dict = _v2_request(2, subtitles_enabled=False)
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)
        assert ctx.render_spec.defaults.subtitles.enabled is False

    def test_subtitle_global_enabled(self, tmp_path: Path) -> None:
        req_dict = _v2_request(2, subtitles_enabled=True)
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)
        assert ctx.render_spec.defaults.subtitles.enabled is True

    def test_subtitle_scene_override(self, tmp_path: Path) -> None:
        """Scene-level subtitle enabled=True overrides global enabled=False."""
        req_dict = _v2_request(2, subtitles_enabled=False)
        req_dict["render_spec"]["scenes"][0]["subtitle"] = {
            "enabled": True,
            "source": "narration",
        }
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)
        assert ctx.render_spec.scenes[0].subtitle is not None
        assert ctx.render_spec.scenes[0].subtitle.enabled is True

    def test_subtitle_scene_override_off(self, tmp_path: Path) -> None:
        """Scene-level subtitle enabled=False overrides global enabled=True."""
        req_dict = _v2_request(2, subtitles_enabled=True)
        req_dict["render_spec"]["scenes"][1]["subtitle"] = {
            "enabled": False,
            "source": "narration",
        }
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)
        assert ctx.render_spec.scenes[1].subtitle.enabled is False

    def test_subtitle_custom_with_text(self, tmp_path: Path) -> None:
        req_dict = _v2_request(1)
        req_dict["render_spec"]["scenes"][0]["subtitle"] = {
            "enabled": True,
            "source": "custom",
            "text": "Custom subtitle.",
        }
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)

    def test_subtitle_custom_without_text_fails(self, tmp_path: Path) -> None:
        req_dict = _v2_request(1)
        req_dict["render_spec"]["scenes"][0]["subtitle"] = {
            "enabled": True,
            "source": "custom",
            "text": "",
        }
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        with pytest.raises(StageError, match="subtitle.source='custom' but text is empty"):
            stage.execute(ctx)

    def test_subtitle_invalid_source_fails(self, tmp_path: Path) -> None:
        req_dict = _v2_request(1)
        req_dict["render_spec"]["scenes"][0]["subtitle"] = {
            "enabled": True,
            "source": "invalid_source",
        }
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        with pytest.raises(StageError, match="subtitle.source='invalid_source'"):
            stage.execute(ctx)


# ── Missing asset policies ───────────────────────────────────────────────────


class TestV2MissingAssetPolicy:
    """Validate layer roles and missing_asset_policy values."""

    @pytest.mark.parametrize(
        "policy", ["drop_layer", "skip_scene", "scene_placeholder", "fail_request"]
    )
    def test_valid_policies_accepted(self, tmp_path: Path, policy: str) -> None:
        req_dict = _v2_request(1, missing_asset_policy=policy, planning_count=1)
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)

    def test_invalid_policy_fails(self, tmp_path: Path) -> None:
        req_dict = _v2_request(1, planning_count=1)
        req_dict["render_spec"]["scenes"][0]["layers"][0]["missing_asset_policy"] = "nope"
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        with pytest.raises(StageError, match="unknown.*missing_asset_policy"):
            stage.execute(ctx)


# ── Layer roles ──────────────────────────────────────────────────────────────


class TestV2LayerRoles:
    """Validate layer semantic roles."""

    @pytest.mark.parametrize(
        "role",
        ["primary_visual", "supporting_visual", "title", "logo", "cta", "decorative_text"],
    )
    def test_valid_roles_accepted(self, tmp_path: Path, role: str) -> None:
        req_dict = _v2_request(1, planning_count=1)
        req_dict["render_spec"]["scenes"][0]["layers"][0]["role"] = role
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)

    def test_null_role_accepted(self, tmp_path: Path) -> None:
        """V1-style layers with no role are accepted in V2."""
        req_dict = _v2_request(1, roles=False, planning_count=1)
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)

    def test_invalid_role_fails(self, tmp_path: Path) -> None:
        req_dict = _v2_request(1, planning_count=1)
        req_dict["render_spec"]["scenes"][0]["layers"][0]["role"] = "not_a_role"
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        with pytest.raises(StageError, match="unknown role='not_a_role'"):
            stage.execute(ctx)


# ── Planning metadata ────────────────────────────────────────────────────────


class TestV2PlanningMetadata:
    """Validate planning.planned_scene_count matching."""

    def test_matching_count_passes(self, tmp_path: Path) -> None:
        req_dict = _v2_request(3, planning_count=3)
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)

    def test_mismatched_count_fails(self, tmp_path: Path) -> None:
        req_dict = _v2_request(3, planning_count=5)
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        with pytest.raises(StageError, match="planned_scene_count=5 but render_spec has 3"):
            stage.execute(ctx)

    def test_no_planning_passes(self, tmp_path: Path) -> None:
        """V2 without planning block is valid."""
        req_dict = _v2_request(3)
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)

    def test_planning_without_count_passes(self, tmp_path: Path) -> None:
        """planning present but planned_scene_count=null is valid."""
        req_dict = _v2_request(3, planning_count=3)
        req_dict["planning"]["planned_scene_count"] = None
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)


# ── Text-only (intentional) scene ────────────────────────────────────────────


class TestV2TextOnlyScene:
    """A scene with only text layers and no primary_visual is valid in V2."""

    def test_text_only_scene_no_primary_visual(self, tmp_path: Path) -> None:
        req_dict = _v2_request(1, planning_count=1)
        # Replace primary_visual with a text-only layer
        req_dict["render_spec"]["scenes"][0]["layers"] = [
            {
                "layer_id": "l_title",
                "type": "text",
                "text": "Title text only scene",
                "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920},
                "role": "title",
                "required": False,
                "missing_asset_policy": "drop_layer",
            }
        ]
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)
        assert ctx.render_spec.scenes[0].layers[0].role == "title"


# ── Backward compatibility ───────────────────────────────────────────────────


class TestV2BackwardCompatibility:
    """V1-style requests without V2 fields are still valid."""

    def test_v1_request_still_valid(self, tmp_path: Path) -> None:
        """A V1 request with no roles/planning/subtitles validates."""
        spec_dict = {
            "spec_version": "1.0",
            "canvas": {"w": 1080, "h": 1920, "fps": 30, "bg": "#000000"},
            "defaults": {
                "narration": {"lang": "vi-VN"},
                "scene_timing": {},
            },
            "assets": [
                {
                    "id": "a1",
                    "type": "image",
                    "source": {"kind": "http", "url": "https://example.com/1.jpg"},
                }
            ],
            "scenes": [
                {
                    "scene_id": "s1",
                    "narration": {"text": "V1 narration."},
                    "layers": [
                        {
                            "layer_id": "l1",
                            "type": "image",
                            "asset_ref": "a1",
                            "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920},
                        }
                    ],
                }
            ],
        }
        req_dict = {"job_id": "test-v1", "spec_version": "1.0", "render_spec": spec_dict}
        ctx = _make_ctx(tmp_path, req_dict)
        stage = ValidateStage()
        stage.execute(ctx)
        assert ctx.render_spec.scenes[0].layers[0].role is None
        assert ctx.render_spec.scenes[0].layers[0].required is False
        assert ctx.render_spec.scenes[0].layers[0].missing_asset_policy == "drop_layer"
