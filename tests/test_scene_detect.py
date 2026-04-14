"""Tests for SCENE_DETECT – FFmpeg parser, post-processing, stage, and regression."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maker8.models.common import RenderStage
from maker8.models.contracts import RenderRequest
from maker8.observability.metrics import _STAGE_ORDINALS
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.scene_detect import (
    SceneDetectStage,
    _build_detect_cmd,
    _parse_showinfo_output,
    _probe_duration,
    detect_scenes,
    post_process_candidates,
)
from maker8.retry import RENDER_RETRYABLE_STAGES, StageError
from render_contracts.render_spec import (
    AssetSourceOptions,
    RenderSpec,
    SceneBoundary,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_spec(
    assets: list[dict] | None = None,
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
            "assets": assets or [],
            "scenes": [],
        }
    )


def _make_video_asset(
    asset_id: str = "v1",
    scene_detect_enabled: bool = True,
    **kwargs: object,
) -> dict:
    opts: dict = {"scene_detect_enabled": scene_detect_enabled}
    opts.update(kwargs)
    return {
        "id": asset_id,
        "type": "video",
        "source": {"kind": "http", "url": "http://example.com/v.mp4", "options": opts},
    }


def _make_ctx(
    assets: list[dict] | None = None,
    downloaded: dict[str, Path] | None = None,
    failed: set[str] | None = None,
) -> PipelineContext:
    spec = _make_spec(assets or [])
    ctx = PipelineContext(
        job_id="test-job",
        render_spec=spec,
        work_dir=Path("/tmp/test"),
        assets_dir=Path("/tmp/test/assets"),
        tts_dir=Path("/tmp/test/tts"),
        output_dir=Path("/tmp/test/output"),
    )
    if downloaded:
        ctx.downloaded_assets.update(downloaded)
    if failed:
        ctx.failed_assets.update(failed)
    return ctx


# ═════════════════════════════════════════════════════════════════════════════
# SD-2: FFmpeg command builder & parser
# ═════════════════════════════════════════════════════════════════════════════


class TestBuildDetectCmd:
    @patch("maker8.pipeline.scene_detect.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    def test_default_params(self, _mock: MagicMock) -> None:
        cmd = _build_detect_cmd(Path("/video.mp4"))
        assert cmd[0] == "/usr/bin/ffmpeg"
        assert "-hide_banner" in cmd
        assert "-i" in cmd
        vf_idx = cmd.index("-vf")
        vf = cmd[vf_idx + 1]
        assert "fps=3" in vf
        assert "scale=640:-2" in vf
        assert "gt(scene" in vf
        assert "0.35" in vf
        assert "showinfo" in vf

    @patch("maker8.pipeline.scene_detect.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    def test_custom_params(self, _mock: MagicMock) -> None:
        cmd = _build_detect_cmd(
            Path("/video.mp4"),
            threshold=0.5,
            sample_fps=5,
            scale_width=320,
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "fps=5" in vf
        assert "scale=320:-2" in vf
        assert "0.5" in vf

    @patch("maker8.pipeline.scene_detect.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    def test_output_goes_to_null(self, _mock: MagicMock) -> None:
        cmd = _build_detect_cmd(Path("/video.mp4"))
        assert (
            cmd[-2:]
            == [
                "-",
            ]
            or "-f" in cmd
        )
        null_idx = cmd.index("null")
        assert cmd[null_idx - 1] == "-f"
        assert cmd[null_idx + 1] == "-"


class TestParseShowinfoOutput:
    def test_valid_showinfo_lines(self) -> None:
        stderr = (
            "[Parsed_showinfo_3 @ 0x] n:0 pts:1234 pts_time:5.123 ...\n"
            "[Parsed_showinfo_3 @ 0x] n:1 pts:2468 pts_time:10.500 ...\n"
        )
        result = _parse_showinfo_output(stderr)
        assert result == [5.123, 10.5]

    def test_empty_input(self) -> None:
        assert _parse_showinfo_output("") == []

    def test_no_showinfo_lines(self) -> None:
        stderr = "frame=100 fps=30 q=28.0 size=0kB time=00:00:03.33\n"
        assert _parse_showinfo_output(stderr) == []

    def test_deduplicates(self) -> None:
        stderr = "[showinfo] pts_time:5.0\n[showinfo] pts_time:5.0\n[showinfo] pts_time:10.0\n"
        result = _parse_showinfo_output(stderr)
        assert result == [5.0, 10.0]

    def test_sorted_output(self) -> None:
        stderr = "[showinfo] pts_time:10.0\n[showinfo] pts_time:3.0\n[showinfo] pts_time:7.0\n"
        result = _parse_showinfo_output(stderr)
        assert result == [3.0, 7.0, 10.0]

    def test_skips_negative_timestamps(self) -> None:
        stderr = "[showinfo] pts_time:-1.0\n[showinfo] pts_time:5.0\n"
        result = _parse_showinfo_output(stderr)
        assert result == [5.0]

    def test_pts_time_in_line_without_showinfo_tag(self) -> None:
        stderr = "some random line pts_time:3.5\n"
        result = _parse_showinfo_output(stderr)
        assert result == [3.5]

    def test_malformed_pts_time(self) -> None:
        stderr = "[showinfo] pts_time:abc\n[showinfo] pts_time:5.0\n"
        result = _parse_showinfo_output(stderr)
        assert result == [5.0]


class TestDetectScenes:
    @patch("maker8.pipeline.scene_detect.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("maker8.pipeline.scene_detect.subprocess.run")
    def test_success_extracts_timestamps(self, mock_run: MagicMock, _ffmpeg: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stderr="[showinfo] pts_time:5.0\n[showinfo] pts_time:10.0\n",
        )
        result = detect_scenes(Path("/video.mp4"))
        assert result == [5.0, 10.0]
        mock_run.assert_called_once()

    @patch("maker8.pipeline.scene_detect.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("maker8.pipeline.scene_detect.subprocess.run")
    def test_empty_on_no_scene_changes(self, mock_run: MagicMock, _ffmpeg: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="frame=100\n")
        result = detect_scenes(Path("/video.mp4"))
        assert result == []

    @patch("maker8.pipeline.scene_detect.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("maker8.pipeline.scene_detect.subprocess.run")
    def test_timeout_raises_retryable_stage_error(
        self, mock_run: MagicMock, _ffmpeg: MagicMock
    ) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120)
        with pytest.raises(StageError) as exc_info:
            detect_scenes(Path("/video.mp4"))
        assert exc_info.value.code == "SCENE_DETECT_TIMEOUT"
        assert exc_info.value.retryable is True

    @patch("maker8.pipeline.scene_detect.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("maker8.pipeline.scene_detect.subprocess.run")
    def test_ffmpeg_error_raises_non_retryable(
        self, mock_run: MagicMock, _ffmpeg: MagicMock
    ) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="Error opening file")
        with pytest.raises(StageError) as exc_info:
            detect_scenes(Path("/video.mp4"))
        assert exc_info.value.code == "SCENE_DETECT_FFMPEG_ERROR"
        assert exc_info.value.retryable is False

    @patch("maker8.pipeline.scene_detect.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("maker8.pipeline.scene_detect.subprocess.run")
    def test_uses_custom_options(self, mock_run: MagicMock, _ffmpeg: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        opts = AssetSourceOptions(
            scene_detect_enabled=True,
            scene_detect_threshold=0.5,
            scene_detect_sample_fps=5,
            scene_detect_scale_width=320,
        )
        detect_scenes(Path("/video.mp4"), opts)
        cmd = mock_run.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        assert "0.5" in vf
        assert "fps=5" in vf
        assert "scale=320" in vf


# ═════════════════════════════════════════════════════════════════════════════
# SD-3: Post-processing policy
# ═════════════════════════════════════════════════════════════════════════════


class TestPostProcessCandidates:
    def test_basic_intervals(self) -> None:
        result = post_process_candidates([5.0, 10.0], 30.0)
        assert len(result) == 3
        assert result[0].start_sec == 0.0
        assert result[0].end_sec == 5.0
        assert result[-1].end_sec == 30.0

    def test_empty_timestamps_returns_full_video(self) -> None:
        result = post_process_candidates([], 30.0)
        assert len(result) == 1
        assert result[0].start_sec == 0.0
        assert result[0].end_sec == 30.0

    def test_single_timestamp(self) -> None:
        result = post_process_candidates([15.0], 30.0)
        assert len(result) == 2
        assert result[0] == SceneBoundary(start_sec=0.0, end_sec=15.0)
        assert result[1] == SceneBoundary(start_sec=15.0, end_sec=30.0)

    def test_coverage_equals_duration(self) -> None:
        result = post_process_candidates([3.0, 8.0, 16.0], 20.0)
        total = sum(b.end_sec - b.start_sec for b in result)
        assert abs(total - 20.0) < 0.01

    def test_dedup_near_timestamps(self) -> None:
        result = post_process_candidates([5.0, 5.05, 10.0], 30.0)
        # 5.0 and 5.05 should be deduped (within 0.1s tolerance)
        assert len(result) == 3  # [0,5], [5,10], [10,30]

    def test_clamping_beyond_duration(self) -> None:
        result = post_process_candidates([5.0, 50.0], 30.0)
        # 50.0 clamped to 30.0
        assert result[-1].end_sec == 30.0

    def test_max_scenes_limits(self) -> None:
        result = post_process_candidates([5.0, 10.0, 15.0, 20.0], 30.0, max_scenes=3)
        assert len(result) == 3

    def test_merge_short_scenes(self) -> None:
        # 0.5s scene should be merged
        result = post_process_candidates([0.5, 5.0, 10.0], 30.0, min_scene_len_sec=1.0)
        # The 0-0.5 scene is < 1.0s → merged with next
        assert result[0].start_sec == 0.0
        # First interval should be longer than 0.5
        assert result[0].end_sec - result[0].start_sec >= 1.0

    def test_zero_duration(self) -> None:
        result = post_process_candidates([1.0], 0.0)
        assert len(result) == 1
        assert result[0].start_sec == 0.0
        assert result[0].end_sec == 0.0

    def test_no_gaps_or_overlaps(self) -> None:
        result = post_process_candidates([3.0, 7.0, 12.0, 18.0, 25.0], 30.0)
        for i in range(len(result) - 1):
            assert result[i].end_sec == result[i + 1].start_sec
        assert result[0].start_sec == 0.0
        assert result[-1].end_sec == 30.0


# ═════════════════════════════════════════════════════════════════════════════
# SD-4: SceneDetectStage execution
# ═════════════════════════════════════════════════════════════════════════════


class TestSceneDetectStageProperties:
    def test_name_is_scene_detect(self) -> None:
        stage = SceneDetectStage()
        assert stage.name == RenderStage.SCENE_DETECT


class TestSceneDetectStageExecution:
    @patch("maker8.pipeline.scene_detect._probe_duration", return_value=30.0)
    @patch("maker8.pipeline.scene_detect.detect_scenes", return_value=[5.0, 10.0])
    def test_happy_path_populates_candidates(
        self, mock_detect: MagicMock, mock_probe: MagicMock
    ) -> None:
        ctx = _make_ctx(
            assets=[_make_video_asset("v1")],
            downloaded={"v1": Path("/tmp/v1.mp4")},
        )
        SceneDetectStage().execute(ctx)
        assert "v1" in ctx.scene_candidates
        assert len(ctx.scene_candidates["v1"]) == 3  # [0,5], [5,10], [10,30]
        assert len(ctx.scene_detect_reports) == 1
        assert ctx.scene_detect_reports[0]["status"] == "ok"

    def test_skips_non_video_assets(self) -> None:
        ctx = _make_ctx(
            assets=[
                {"id": "img1", "type": "image", "source": {"kind": "http", "url": "http://x/i.png"}}
            ],
            downloaded={"img1": Path("/tmp/img1.png")},
        )
        SceneDetectStage().execute(ctx)
        assert ctx.scene_candidates == {}
        assert ctx.scene_detect_reports == []

    def test_skips_disabled_scene_detect(self) -> None:
        ctx = _make_ctx(
            assets=[_make_video_asset("v1", scene_detect_enabled=False)],
            downloaded={"v1": Path("/tmp/v1.mp4")},
        )
        SceneDetectStage().execute(ctx)
        assert ctx.scene_candidates == {}

    def test_skips_failed_assets(self) -> None:
        ctx = _make_ctx(
            assets=[_make_video_asset("v1")],
            downloaded={"v1": Path("/tmp/v1.mp4")},
            failed={"v1"},
        )
        SceneDetectStage().execute(ctx)
        assert ctx.scene_candidates == {}

    def test_skips_not_downloaded_asset(self) -> None:
        ctx = _make_ctx(
            assets=[_make_video_asset("v1")],
            downloaded={},
        )
        SceneDetectStage().execute(ctx)
        assert ctx.scene_candidates == {}

    @patch("maker8.pipeline.scene_detect._probe_duration", return_value=30.0)
    @patch(
        "maker8.pipeline.scene_detect.detect_scenes",
        side_effect=StageError(
            stage=RenderStage.SCENE_DETECT,
            code="SCENE_DETECT_FFMPEG_ERROR",
            message="fail",
            retryable=False,
        ),
    )
    def test_detection_failure_adds_warning_and_continues(
        self, mock_detect: MagicMock, mock_probe: MagicMock
    ) -> None:
        ctx = _make_ctx(
            assets=[_make_video_asset("v1"), _make_video_asset("v2")],
            downloaded={"v1": Path("/tmp/v1.mp4"), "v2": Path("/tmp/v2.mp4")},
        )
        # First call fails, second succeeds
        mock_detect.side_effect = [
            StageError(
                stage=RenderStage.SCENE_DETECT,
                code="SCENE_DETECT_FFMPEG_ERROR",
                message="fail",
                retryable=False,
            ),
            [5.0],  # success for v2
        ]
        SceneDetectStage().execute(ctx)
        # v1 failed
        assert any(w.code == "SCENE_DETECT_FAILED" and w.asset_id == "v1" for w in ctx.warnings)
        # v2 succeeded
        assert "v2" in ctx.scene_candidates
        assert "v1" not in ctx.scene_candidates

    @patch("maker8.pipeline.scene_detect._probe_duration", return_value=30.0)
    @patch("maker8.pipeline.scene_detect.detect_scenes", return_value=[])
    def test_empty_detection_adds_warning(
        self, mock_detect: MagicMock, mock_probe: MagicMock
    ) -> None:
        ctx = _make_ctx(
            assets=[_make_video_asset("v1")],
            downloaded={"v1": Path("/tmp/v1.mp4")},
        )
        SceneDetectStage().execute(ctx)
        assert "v1" in ctx.scene_candidates
        # Full-video fallback
        assert len(ctx.scene_candidates["v1"]) == 1
        assert ctx.scene_candidates["v1"][0].start_sec == 0.0
        assert ctx.scene_candidates["v1"][0].end_sec == 30.0
        # Warning present
        assert any(w.code == "SCENE_DETECT_EMPTY" for w in ctx.warnings)

    @patch("maker8.pipeline.scene_detect._probe_duration", return_value=None)
    @patch("maker8.pipeline.scene_detect.detect_scenes", return_value=[5.0])
    def test_probe_failure_adds_warning(
        self, mock_detect: MagicMock, mock_probe: MagicMock
    ) -> None:
        ctx = _make_ctx(
            assets=[_make_video_asset("v1")],
            downloaded={"v1": Path("/tmp/v1.mp4")},
        )
        SceneDetectStage().execute(ctx)
        assert "v1" not in ctx.scene_candidates
        assert any(w.code == "SCENE_DETECT_PROBE_FAILED" for w in ctx.warnings)
        assert ctx.scene_detect_reports[0]["status"] == "probe_failed"

    @patch("maker8.pipeline.scene_detect._probe_duration", return_value=30.0)
    @patch("maker8.pipeline.scene_detect.detect_scenes", return_value=[5.0, 15.0])
    def test_report_contains_expected_fields(
        self, mock_detect: MagicMock, mock_probe: MagicMock
    ) -> None:
        ctx = _make_ctx(
            assets=[_make_video_asset("v1")],
            downloaded={"v1": Path("/tmp/v1.mp4")},
        )
        SceneDetectStage().execute(ctx)
        report = ctx.scene_detect_reports[0]
        assert report["asset_id"] == "v1"
        assert report["status"] == "ok"
        assert report["raw_count"] == 2
        assert report["boundary_count"] == 3
        assert report["duration"] == 30.0


# ═════════════════════════════════════════════════════════════════════════════
# Probe duration
# ═════════════════════════════════════════════════════════════════════════════


class TestProbeDuration:
    @patch("maker8.pipeline.scene_detect.resolve_ffprobe_binary", return_value="/usr/bin/ffprobe")
    @patch("maker8.pipeline.scene_detect.subprocess.run")
    def test_success(self, mock_run: MagicMock, _ffprobe: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="30.500\n")
        result = _probe_duration(Path("/video.mp4"))
        assert result == 30.5

    @patch("maker8.pipeline.scene_detect.resolve_ffprobe_binary", return_value="/usr/bin/ffprobe")
    @patch("maker8.pipeline.scene_detect.subprocess.run")
    def test_failure_returns_none(self, mock_run: MagicMock, _ffprobe: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _probe_duration(Path("/video.mp4")) is None

    @patch("maker8.pipeline.scene_detect.resolve_ffprobe_binary", return_value="/usr/bin/ffprobe")
    @patch("maker8.pipeline.scene_detect.subprocess.run")
    def test_timeout_returns_none(self, mock_run: MagicMock, _ffprobe: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffprobe", timeout=15)
        assert _probe_duration(Path("/video.mp4")) is None


# ═════════════════════════════════════════════════════════════════════════════
# Regression & consistency
# ═════════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    def test_asset_source_options_without_scene_detect_fields(self) -> None:
        opts = AssetSourceOptions()
        assert opts.scene_detect_enabled is False
        assert opts.scene_detect_threshold is None
        assert opts.scene_detect_min_scene_len_sec is None
        assert opts.scene_detect_max_scenes is None

    def test_old_render_request_without_scene_detect(self) -> None:
        payload = {
            "job_id": "old-job",
            "render_spec": {
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
                    {
                        "id": "v1",
                        "type": "video",
                        "source": {"kind": "http", "url": "http://x/v.mp4"},
                    },
                ],
                "scenes": [],
            },
        }
        req = RenderRequest.model_validate(payload)
        asset = req.render_spec.assets[0]
        assert asset.source.options.scene_detect_enabled is False

    def test_pipeline_context_empty_scene_fields(self) -> None:
        ctx = PipelineContext(
            job_id="test",
            render_spec=_make_spec(),
        )
        assert ctx.scene_candidates == {}
        assert ctx.scene_detect_reports == []


class TestConsistencyChecks:
    def test_scene_detect_in_render_stage_enum(self) -> None:
        assert hasattr(RenderStage, "SCENE_DETECT")
        assert RenderStage.SCENE_DETECT.value == "SCENE_DETECT"

    def test_scene_detect_in_retryable_stages(self) -> None:
        assert RenderStage.SCENE_DETECT in RENDER_RETRYABLE_STAGES

    def test_scene_detect_in_stage_ordinals(self) -> None:
        assert RenderStage.SCENE_DETECT in _STAGE_ORDINALS
        # SCENE_DETECT should be between DOWNLOAD and NORMALIZE
        assert _STAGE_ORDINALS[RenderStage.SCENE_DETECT] > _STAGE_ORDINALS[RenderStage.DOWNLOAD]
        assert _STAGE_ORDINALS[RenderStage.SCENE_DETECT] < _STAGE_ORDINALS[RenderStage.NORMALIZE]

    def test_all_render_stages_have_ordinals(self) -> None:
        for stage in RenderStage:
            assert stage in _STAGE_ORDINALS, f"Missing ordinal for {stage}"

    def test_ordinals_are_unique(self) -> None:
        ordinals = list(_STAGE_ORDINALS.values())
        assert len(ordinals) == len(set(ordinals))

    def test_orchestrator_has_scene_detect_stage(self) -> None:
        import inspect

        from maker8.pipeline.orchestrator import Orchestrator

        src = inspect.getsource(Orchestrator.__init__)
        assert "SceneDetectStage()" in src
        # Verify ordering
        dl_pos = src.index("DownloadStage")
        sd_pos = src.index("SceneDetectStage")
        nm_pos = src.index("NormalizeStage")
        assert dl_pos < sd_pos < nm_pos

    def test_scene_detect_in_dry_run_skip(self) -> None:
        from maker8.pipeline.orchestrator import Orchestrator

        assert RenderStage.SCENE_DETECT in Orchestrator._DRY_RUN_SKIP
