"""Contract compatibility tests – golden fixture round-trip validation.

These tests ensure that the wire-format JSON produced by editor8 can be
parsed by maker8's ``RenderRequest`` model, and that round-trip serialization
preserves the payload byte-for-byte (ignoring key ordering).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maker8.models.contracts import RenderRequest
from maker8.models.spec import (
    Asset,
    AudioTrack,
    Canvas,
    Defaults,
    EffectInstance,
    Layer,
    OutputConfig,
    PublishConfig,
    PublishTarget,
    Rect,
    RenderSpec,
    ResultDestination,
    SafeArea,
    Scene,
    SceneNarration,
    SceneTiming,
    TextStyle,
    Trace,
    Transition,
    Trim,
)
from render_contracts.render_spec import RenderRequest as CanonicalRenderRequest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


# ── Fixture parametrization ──────────────────────────────────────────────────

GOLDEN_FILES = sorted(FIXTURES_DIR.glob("golden_*.json"))


@pytest.fixture(params=[f.name for f in GOLDEN_FILES], ids=[f.stem for f in GOLDEN_FILES])
def golden_payload(request: pytest.FixtureRequest) -> dict:
    return _load_fixture(request.param)


# ── Tests ────────────────────────────────────────────────────────────────────


class TestGoldenFixtureParsing:
    """Parse golden JSON payloads with both canonical and maker8 models."""

    def test_canonical_model_parses(self, golden_payload: dict) -> None:
        req = CanonicalRenderRequest.model_validate(golden_payload)
        assert req.job_id == golden_payload["job_id"]

    def test_maker8_model_parses(self, golden_payload: dict) -> None:
        req = RenderRequest.model_validate(golden_payload)
        assert req.job_id == golden_payload["job_id"]

    def test_models_produce_identical_output(self, golden_payload: dict) -> None:
        canonical = CanonicalRenderRequest.model_validate(golden_payload)
        maker8 = RenderRequest.model_validate(golden_payload)

        canonical_dict = canonical.model_dump(mode="json", by_alias=True)
        maker8_dict = maker8.model_dump(mode="json", by_alias=True)
        assert canonical_dict == maker8_dict


class TestRoundTrip:
    """Serialise → deserialise round-trip preserves content."""

    def test_round_trip_preserves_payload(self, golden_payload: dict) -> None:
        req = RenderRequest.model_validate(golden_payload)
        dumped = req.model_dump(mode="json", by_alias=True)
        req2 = RenderRequest.model_validate(dumped)
        assert req == req2

    def test_by_alias_matches_wire_keys(self, golden_payload: dict) -> None:
        """Alias fields (e.g. Trim.in) appear correctly in serialized output."""
        req = RenderRequest.model_validate(golden_payload)
        dumped = json.dumps(req.model_dump(mode="json", by_alias=True))
        parsed = json.loads(dumped)

        for scene in parsed["render_spec"]["scenes"]:
            for layer in scene["layers"]:
                if layer.get("trim"):
                    assert "in" in layer["trim"], "Trim alias 'in' missing in wire output"
                    assert "in_" not in layer["trim"], "Python field 'in_' leaked to wire"


class TestModelIdentity:
    """Verify that maker8 re-exports are the exact same classes as render_contracts."""

    def test_render_request_is_canonical(self) -> None:
        assert RenderRequest is CanonicalRenderRequest

    def test_all_reexported_types_are_canonical(self) -> None:
        from render_contracts import render_spec as canonical

        pairs = [
            (Asset, canonical.Asset),
            (AudioTrack, canonical.AudioTrack),
            (Canvas, canonical.Canvas),
            (Defaults, canonical.Defaults),
            (EffectInstance, canonical.EffectInstance),
            (Layer, canonical.Layer),
            (OutputConfig, canonical.OutputConfig),
            (PublishConfig, canonical.PublishConfig),
            (PublishTarget, canonical.PublishTarget),
            (Rect, canonical.Rect),
            (RenderSpec, canonical.RenderSpec),
            (ResultDestination, canonical.ResultDestination),
            (SafeArea, canonical.SafeArea),
            (Scene, canonical.Scene),
            (SceneNarration, canonical.SceneNarration),
            (SceneTiming, canonical.SceneTiming),
            (TextStyle, canonical.TextStyle),
            (Trace, canonical.Trace),
            (Transition, canonical.Transition),
            (Trim, canonical.Trim),
        ]
        for maker8_cls, canonical_cls in pairs:
            assert maker8_cls is canonical_cls, (
                f"{maker8_cls.__name__} from maker8 is not the canonical class"
            )


class TestFieldCoverage:
    """Verify that golden fixtures exercise key fields."""

    def test_minimal_fixture_has_required_structure(self) -> None:
        payload = _load_fixture("golden_minimal_request.json")
        req = RenderRequest.model_validate(payload)
        spec = req.render_spec

        assert spec.canvas.w > 0
        assert spec.canvas.h > 0
        assert len(spec.assets) >= 1
        assert len(spec.scenes) >= 1
        assert spec.scenes[0].narration.text

    def test_multiscene_fixture_covers_advanced_features(self) -> None:
        payload = _load_fixture("golden_multiscene_request.json")
        req = RenderRequest.model_validate(payload)
        spec = req.render_spec

        # Canvas with safe_area
        assert spec.canvas.safe_area is not None

        # Multiple asset types
        asset_types = {a.type for a in spec.assets}
        assert "video" in asset_types
        assert "image" in asset_types
        assert "audio" in asset_types

        # YouTube source
        youtube = [a for a in spec.assets if a.source.kind == "youtube"]
        assert len(youtube) >= 1

        # Layers with trim (alias "in")
        intro = spec.scenes[0]
        video_layers = [ly for ly in intro.layers if ly.trim is not None]
        assert len(video_layers) >= 1
        assert video_layers[0].trim.in_ == 10.0

        # Text layers with style
        text_layers = [ly for ly in intro.layers if ly.type == "text"]
        assert len(text_layers) >= 1
        assert text_layers[0].style is not None
        assert text_layers[0].style.stroke_color is not None

        # Audio tracks
        assert len(intro.audio_tracks) >= 1

        # Effects
        assert len(intro.effects) >= 1

        # Transitions
        assert intro.transition_out is not None

        # Publish targets
        assert len(spec.publish.targets) >= 1

        # Multiple scenes
        assert len(spec.scenes) >= 3


class TestEditor8CrossRepoContract:
    """Validate that editor8-produced payloads with all new fields parse correctly.

    This fixture exercises: dry_run, canvas_profile, publish_intent,
    uploader_metadata (with source_attributions), and result destination routing.
    """

    def test_editor8_full_payload_parses(self) -> None:
        payload = _load_fixture("golden_editor8_full_request.json")
        req = RenderRequest.model_validate(payload)
        assert req.job_id == "golden-editor8-full-001"

    def test_dry_run_preserved(self) -> None:
        payload = _load_fixture("golden_editor8_full_request.json")
        req = RenderRequest.model_validate(payload)
        assert req.dry_run is False

    def test_canvas_profile_preserved(self) -> None:
        payload = _load_fixture("golden_editor8_full_request.json")
        req = RenderRequest.model_validate(payload)
        assert req.canvas_profile == "portrait_1080x1920"

    def test_publish_intent_preserved(self) -> None:
        payload = _load_fixture("golden_editor8_full_request.json")
        req = RenderRequest.model_validate(payload)
        assert req.publish_intent == "render_and_publish"

    def test_uploader_metadata_fully_parsed(self) -> None:
        payload = _load_fixture("golden_editor8_full_request.json")
        req = RenderRequest.model_validate(payload)
        um = req.uploader_metadata
        assert um.channel_id == "yt:test-channel"
        assert um.title == "Test Video Title"
        assert um.lang == "vi"
        assert um.visibility == "private"
        assert len(um.keywords) == 2
        assert len(um.source_attributions) == 1
        assert um.source_attributions[0].provider == "youtube"

    def test_result_destination_routing_fields(self) -> None:
        payload = _load_fixture("golden_editor8_full_request.json")
        req = RenderRequest.model_validate(payload)
        assert req.result.topic == "video.render.result.v1"
        assert req.result.key == "golden-editor8-full-001"

    def test_round_trip_preserves_new_fields(self) -> None:
        payload = _load_fixture("golden_editor8_full_request.json")
        req = RenderRequest.model_validate(payload)
        dumped = req.model_dump(mode="json", by_alias=True)
        req2 = RenderRequest.model_validate(dumped)
        assert req2.dry_run == req.dry_run
        assert req2.canvas_profile == req.canvas_profile
        assert req2.publish_intent == req.publish_intent
        assert req2.uploader_metadata == req.uploader_metadata
        assert req2.result.topic == req.result.topic
        assert req2.result.key == req.result.key
