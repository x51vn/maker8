"""Tests for XST-1056: contract field labeling – RESERVED fields and importability."""

from __future__ import annotations


class TestContractReservedFieldsAccepted:
    """RESERVED fields must not raise validation errors when set."""

    def test_reserved_duration_mode_accepted(self) -> None:
        from render_contracts.render_spec import RenderSpec, SceneTiming

        spec = RenderSpec(
            spec_version="1.0",
            defaults={
                "scene_timing": SceneTiming(
                    head_pad_sec=0.1,
                    tail_pad_sec=0.2,
                    duration_mode="fixed",  # non-default RESERVED value
                ),
            },  # type: ignore[arg-type]
        )
        assert spec.defaults.scene_timing.duration_mode == "fixed"

    def test_reserved_align_accepted(self) -> None:
        from render_contracts.render_spec import Layer

        layer = Layer(layer_id="l1", type="image", align="center")
        assert layer.align == "center"

    def test_reserved_variant_accepted(self) -> None:
        from render_contracts.render_spec import PublishTarget

        target = PublishTarget(platform="youtube", channel_id="UCch1", variant="shorts")
        assert target.variant == "shorts"

    def test_reserved_result_type_accepted(self) -> None:
        from render_contracts.render_spec import ResultDestination

        rd = ResultDestination(type="kafka", topic="t", key="k")
        assert rd.type == "kafka"

    def test_reserved_publish_intent_accepted(self) -> None:
        from render_contracts.render_spec import RenderRequest, RenderSpec

        req = RenderRequest(
            job_id="j1",
            render_spec=RenderSpec(),
            publish_intent="publish_ready",
        )
        assert req.publish_intent == "publish_ready"


class TestPublishStageEnumExists:
    """PublishStage must be importable regardless of RESERVED status."""

    def test_publish_stage_importable(self) -> None:
        from maker8.models.common import PublishStage

        assert hasattr(PublishStage, "PUBLISH")
        assert hasattr(PublishStage, "EMIT_RESULT")

    def test_publish_status_importable(self) -> None:
        from maker8.models.common import PublishStatus

        assert hasattr(PublishStatus, "PUBLISHED")
        assert hasattr(PublishStatus, "FAILED")
        assert hasattr(PublishStatus, "PENDING")
