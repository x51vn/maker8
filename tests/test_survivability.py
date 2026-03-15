"""Tests for the degraded-rendering / survivability pipeline.

Verifies that individual asset or scene failures are isolated and do not
crash the entire job – instead, the pipeline degrades gracefully, produces
a partial video, and emits ``JobStatus.PARTIAL``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maker8.models.common import AssetWarning, JobStatus, RenderStage
from maker8.models.contracts import RenderResult
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.download import DownloadStage
from maker8.pipeline.emit import EmitResultStage
from maker8.pipeline.normalize import NormalizeStage
from maker8.pipeline.render import RenderStageImpl
from maker8.pipeline.tts import TTSStage
from maker8.plugins.registry import PluginRegistry
from maker8.retry import StageError

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_spec(**overrides):  # type: ignore[no-untyped-def]
    """Build a minimal RenderSpec dict, applying *overrides* on top."""
    from maker8.models.spec import RenderSpec

    base = {
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
        "assets": overrides.pop("assets", [
            {"id": "a1", "type": "video", "source": {"kind": "http", "url": "https://x.com/1.mp4"}},
            {"id": "a2", "type": "video", "source": {"kind": "http", "url": "https://x.com/2.mp4"}},
        ]),
        "scenes": overrides.pop("scenes", [
            {
                "scene_id": "s1",
                "narration": {"text": "Scene one."},
                "layers": [
                    {"layer_id": "l1", "type": "video", "asset_ref": "a1",
                     "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920}},
                ],
            },
            {
                "scene_id": "s2",
                "narration": {"text": "Scene two."},
                "layers": [
                    {"layer_id": "l2", "type": "video", "asset_ref": "a2",
                     "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920}},
                ],
            },
        ]),
        **overrides,
    }
    return RenderSpec.model_validate(base)


def _make_ctx(tmp_path: Path, **spec_kw):  # type: ignore[no-untyped-def]
    """Build a PipelineContext backed by a temp directory."""
    from maker8.models.common import Trace

    spec = _make_spec(**spec_kw)
    return PipelineContext(
        job_id="test-job",
        render_spec=spec,
        trace=Trace(),
        work_dir=tmp_path,
        assets_dir=tmp_path / "assets",
        tts_dir=tmp_path / "tts",
        output_dir=tmp_path / "output",
    )


# ── AssetWarning model tests ────────────────────────────────────────────────


class TestAssetWarning:
    def test_create_and_serialize(self) -> None:
        w = AssetWarning(
            asset_id="a1",
            scene_id="s1",
            stage="NORMALIZE",
            code="FFMPEG_ERROR",
            message="codec not supported",
            fallback_used="asset_skipped",
        )
        d = w.model_dump(mode="json")
        assert d["asset_id"] == "a1"
        assert d["stage"] == "NORMALIZE"
        assert d["fallback_used"] == "asset_skipped"

    def test_defaults_are_empty_strings(self) -> None:
        w = AssetWarning()
        assert w.asset_id == ""
        assert w.scene_id == ""
        assert w.stage == ""


# ── PipelineContext degradation tracking ─────────────────────────────────────


class TestContextDegradation:
    def test_not_degraded_by_default(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        assert ctx.is_degraded is False
        assert ctx.warnings == []
        assert ctx.failed_assets == set()
        assert ctx.skipped_scenes == set()

    def test_is_degraded_when_warnings(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.warnings.append(AssetWarning(asset_id="a1", stage="DOWNLOAD"))
        assert ctx.is_degraded is True

    def test_failed_assets_tracked(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.failed_assets.add("a1")
        assert "a1" in ctx.failed_assets


# ── DOWNLOAD per-asset isolation ─────────────────────────────────────────────


class TestDownloadIsolation:
    def test_one_asset_fails_other_succeeds(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.ensure_dirs()

        # Create resolved plans for both assets
        plan_a1 = MagicMock()
        plan_a1.source_kind = "http"
        plan_a2 = MagicMock()
        plan_a2.source_kind = "http"
        ctx.resolved_plans = {"a1": plan_a1, "a2": plan_a2}

        # Connector that fails on a1 but succeeds on a2
        fake_file = ctx.assets_dir / "a2.mp4"
        fake_file.write_bytes(b"\x00" * 100)

        connector = MagicMock()
        call_count = 0

        def download_side_effect(plan, dest_dir):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if plan is plan_a1:
                raise ConnectionError("Network timeout for a1")
            return fake_file

        connector.download.side_effect = download_side_effect

        registry = MagicMock(spec=PluginRegistry)
        registry.get_source.return_value = connector

        stage = DownloadStage(registry)
        # Should NOT raise
        stage.execute(ctx)

        # a1 should be failed, a2 should be downloaded
        assert "a1" in ctx.failed_assets
        assert "a2" not in ctx.failed_assets
        assert "a2" in ctx.downloaded_assets
        assert "a1" not in ctx.downloaded_assets
        assert len(ctx.warnings) == 1
        assert ctx.warnings[0].code == "DOWNLOAD_FAILED"
        assert ctx.warnings[0].asset_id == "a1"
        assert ctx.is_degraded is True

    def test_skips_already_failed_assets(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.ensure_dirs()
        ctx.failed_assets.add("a1")

        plan = MagicMock()
        plan.source_kind = "http"
        ctx.resolved_plans = {"a1": plan}

        connector = MagicMock()
        registry = MagicMock(spec=PluginRegistry)
        registry.get_source.return_value = connector

        stage = DownloadStage(registry)
        stage.execute(ctx)

        # Should not attempt download of a1
        connector.download.assert_not_called()


# ── NORMALIZE per-asset isolation ────────────────────────────────────────────


class TestNormalizeIsolation:
    def test_one_asset_fails_other_succeeds(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.ensure_dirs()

        # Simulate downloaded assets
        a1_path = ctx.assets_dir / "a1.mp4"
        a2_path = ctx.assets_dir / "a2.mp4"
        a1_path.write_bytes(b"\x00" * 100)
        a2_path.write_bytes(b"\x00" * 100)
        ctx.downloaded_assets = {"a1": a1_path, "a2": a2_path}

        stage = NormalizeStage()
        # Mock _normalize_asset to fail on a1, succeed on a2
        normalised_a2 = ctx.assets_dir / "a2_normalised.mp4"
        normalised_a2.write_bytes(b"\x00" * 200)

        def mock_normalize(asset_id, asset_type, src, dest_dir, job_id):  # type: ignore[no-untyped-def]
            if asset_id == "a1":
                raise StageError(RenderStage.NORMALIZE, "FFMPEG_ERROR", "codec failure")
            return normalised_a2

        with patch.object(stage, "_normalize_asset", side_effect=mock_normalize):
            stage.execute(ctx)

        assert "a1" in ctx.failed_assets
        assert "a1" not in ctx.normalized_assets
        assert "a2" in ctx.normalized_assets
        assert len(ctx.warnings) == 1
        assert ctx.warnings[0].code == "FFMPEG_ERROR"
        assert ctx.warnings[0].stage == "NORMALIZE"
        assert ctx.is_degraded is True

    def test_skips_already_failed_assets(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.ensure_dirs()
        ctx.failed_assets.add("a1")

        a1_path = ctx.assets_dir / "a1.mp4"
        a1_path.write_bytes(b"\x00" * 100)
        ctx.downloaded_assets = {"a1": a1_path}

        stage = NormalizeStage()
        with patch.object(stage, "_normalize_asset") as mock_norm:
            stage.execute(ctx)
            mock_norm.assert_not_called()


# ── TTS per-scene isolation ──────────────────────────────────────────────────


class TestTTSIsolation:
    def test_one_scene_tts_fails_other_succeeds(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.ensure_dirs()

        tts_service = MagicMock()
        tts_service.next_google_credentials.return_value = None
        tts_service.next_elevenlabs_key.return_value = "key"

        tts_result = MagicMock()
        tts_result.audio_path = ctx.tts_dir / "s2.mp3"
        tts_result.duration_sec = 3.0

        call_count = {"v": 0}

        def synthesize_side_effect(text, lang, preset, out_path, **kw):  # type: ignore[no-untyped-def]
            call_count["v"] += 1
            if "one" in text.lower():
                raise RuntimeError("TTS provider error")
            return tts_result

        tts_service.synthesize.side_effect = synthesize_side_effect

        stage = TTSStage(tts_service)
        # Should NOT raise
        stage.execute(ctx)

        # s1 should have a warning, s2 should have a TTS result
        assert "s1" not in ctx.tts_results
        assert "s2" in ctx.tts_results
        assert len(ctx.warnings) == 1
        assert ctx.warnings[0].code == "TTS_FAILED"
        assert ctx.warnings[0].scene_id == "s1"
        assert ctx.is_degraded is True


# ── RENDER scene-level degradation ───────────────────────────────────────────


class TestRenderSceneDegradation:
    def test_scene_with_all_missing_assets_is_skipped(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.ensure_dirs()

        # a1 was downloaded but a2 was not (failed)
        a1_path = ctx.assets_dir / "a1.mp4"
        a1_path.write_bytes(b"\x00" * 100)
        ctx.downloaded_assets = {"a1": a1_path}
        ctx.failed_assets.add("a2")

        registry = MagicMock(spec=PluginRegistry)
        stage = RenderStageImpl(registry)

        # Mock compose_video to avoid actual rendering
        fake_video = ctx.output_dir / "test-job.mp4"
        fake_video.parent.mkdir(parents=True, exist_ok=True)
        fake_video.write_bytes(b"\x00" * 1000)

        from maker8.models.common import OutputMeta

        mock_meta = OutputMeta(duration=5.0, w=1080, h=1920, fps=30, size_bytes=1000)

        with patch("maker8.pipeline.render.compose_video", return_value=(fake_video, mock_meta)):
            stage.execute(ctx)

        # s2 should be skipped (its only asset a2 is failed)
        assert "s2" in ctx.skipped_scenes
        assert len(ctx.warnings) >= 1
        scene_skip_warnings = [w for w in ctx.warnings if w.code == "SCENE_NO_CONTENT"]
        assert len(scene_skip_warnings) == 1
        assert scene_skip_warnings[0].scene_id == "s2"
        # s1 should still render
        assert ctx.rendered_video == fake_video

    def test_all_scenes_skipped_raises_stage_error(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.ensure_dirs()

        # Both assets failed
        ctx.failed_assets = {"a1", "a2"}

        registry = MagicMock(spec=PluginRegistry)
        stage = RenderStageImpl(registry)

        with pytest.raises(StageError) as exc_info:
            stage.execute(ctx)

        assert exc_info.value.code == "ALL_SCENES_SKIPPED"
        assert exc_info.value.retryable is False

    def test_text_only_scene_always_viable(self, tmp_path: Path) -> None:
        """A scene with only text layers is viable even with no assets."""
        ctx = _make_ctx(
            tmp_path,
            assets=[],
            scenes=[
                {
                    "scene_id": "s1",
                    "narration": {"text": "Text only scene."},
                    "layers": [
                        {"layer_id": "l_text", "type": "text",
                         "rect": {"x": 0, "y": 0, "w": 1080, "h": 200},
                         "text_content": "Hello world",
                         "text_style": {"font_size": 48, "color": "#FFFFFF"}},
                    ],
                },
            ],
        )
        ctx.ensure_dirs()

        registry = MagicMock(spec=PluginRegistry)
        stage = RenderStageImpl(registry)

        fake_video = ctx.output_dir / "test-job.mp4"
        fake_video.parent.mkdir(parents=True, exist_ok=True)
        fake_video.write_bytes(b"\x00" * 1000)

        from maker8.models.common import OutputMeta

        mock_meta = OutputMeta(duration=5.0, w=1080, h=1920, fps=30, size_bytes=1000)

        with patch("maker8.pipeline.render.compose_video", return_value=(fake_video, mock_meta)):
            stage.execute(ctx)

        assert "s1" not in ctx.skipped_scenes
        assert ctx.rendered_video == fake_video


# ── RenderResult includes warnings ───────────────────────────────────────────


class TestRenderResultWarnings:
    def test_warnings_serialized_in_result(self) -> None:
        from maker8.models.common import EngineVersions

        result = RenderResult(
            job_id="j1",
            status=JobStatus.PARTIAL,
            warnings=[
                AssetWarning(asset_id="a1", stage="DOWNLOAD", code="DOWNLOAD_FAILED"),
                AssetWarning(scene_id="s2", stage="TTS", code="TTS_FAILED"),
            ],
            engine_versions=EngineVersions(),
        )
        d = result.model_dump(mode="json", by_alias=True)
        assert d["status"] == "PARTIAL"
        assert len(d["warnings"]) == 2
        assert d["warnings"][0]["code"] == "DOWNLOAD_FAILED"

    def test_empty_warnings_when_fully_successful(self) -> None:
        from maker8.models.common import EngineVersions

        result = RenderResult(
            job_id="j1",
            status=JobStatus.DONE,
            engine_versions=EngineVersions(),
        )
        d = result.model_dump(mode="json", by_alias=True)
        assert d["warnings"] == []


# ── EmitResultStage uses PARTIAL ─────────────────────────────────────────────


class TestEmitResultDegradation:
    def test_build_result_partial_when_degraded(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.warnings.append(AssetWarning(asset_id="a1", stage="DOWNLOAD"))

        result = EmitResultStage._build_result(ctx)
        assert result.status == JobStatus.PARTIAL
        assert len(result.warnings) == 1

    def test_build_result_done_when_clean(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)

        result = EmitResultStage._build_result(ctx)
        assert result.status == JobStatus.DONE
        assert result.warnings == []
