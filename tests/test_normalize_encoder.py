"""Tests for NORMALIZE stage – GPU/CPU encode paths and fallback chain."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from maker8.pipeline.normalize import _analyze_ffmpeg_failure_reason, _build_video_cmd


@pytest.fixture()
def _src_dest(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "input.mp4"
    dest = tmp_path / "output.mp4"
    src.write_bytes(b"\x00" * 100)
    return src, dest


# ── _build_video_cmd: 4 distinct paths ──────────────────────────────────────


class TestBuildVideoCmd:
    """Verify all 4 FFmpeg command variants produce correct arguments."""

    def test_full_gpu_path(self, _src_dest: tuple[Path, Path]) -> None:
        """Full GPU: -hwaccel cuda + -hwaccel_output_format cuda + h264_nvenc."""
        src, dest = _src_dest
        with patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg"):
            cmd = _build_video_cmd(src, dest, use_nvenc=True)
        assert "-hwaccel" in cmd
        assert "cuda" in cmd
        assert "-hwaccel_output_format" in cmd
        assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
        assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "p4"
        assert "-cq" in cmd and cmd[cmd.index("-cq") + 1] == "23"
        assert "+faststart" in " ".join(cmd)

    def test_gpu_with_proxy_omits_hwaccel_output_format(self, _src_dest: tuple[Path, Path]) -> None:
        """GPU + proxy: -hwaccel cuda but NO -hwaccel_output_format cuda."""
        src, dest = _src_dest
        with patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg"):
            cmd = _build_video_cmd(src, dest, use_nvenc=True, proxy_max_short_edge=720)
        assert "-hwaccel" in cmd
        assert "-hwaccel_output_format" not in cmd
        assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
        assert "-vf" in cmd  # scale filter present

    def test_cpu_decode_nvenc_encode(self, _src_dest: tuple[Path, Path]) -> None:
        """CPU decode + NVENC encode: no -hwaccel at all."""
        src, dest = _src_dest
        with patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg"):
            cmd = _build_video_cmd(src, dest, use_nvenc=True, cpu_decode=True)
        assert "-hwaccel" not in cmd
        assert "-hwaccel_output_format" not in cmd
        assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"

    def test_full_cpu_path(self, _src_dest: tuple[Path, Path]) -> None:
        """Pure software: libx264, no NVENC, no hwaccel."""
        src, dest = _src_dest
        with patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg"):
            cmd = _build_video_cmd(src, dest, use_nvenc=False)
        assert "-hwaccel" not in cmd
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "fast"
        assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "23"
        assert "+faststart" in " ".join(cmd)

    def test_cpu_path_with_proxy(self, _src_dest: tuple[Path, Path]) -> None:
        """CPU + proxy: scale filter present, libx264 encode."""
        src, dest = _src_dest
        with patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg"):
            cmd = _build_video_cmd(src, dest, use_nvenc=False, proxy_max_short_edge=540)
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert "-vf" in cmd

    def test_all_paths_include_aac_audio(self, _src_dest: tuple[Path, Path]) -> None:
        """All paths must include AAC audio encoding at 192k."""
        src, dest = _src_dest
        for kwargs in [
            {"use_nvenc": True},
            {"use_nvenc": True, "cpu_decode": True},
            {"use_nvenc": False},
        ]:
            with patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg"):
                cmd = _build_video_cmd(src, dest, **kwargs)
            assert cmd[cmd.index("-c:a") + 1] == "aac"
            assert cmd[cmd.index("-b:a") + 1] == "192k"

    def test_all_paths_include_overwrite(self, _src_dest: tuple[Path, Path]) -> None:
        """All paths must include -y for overwrite."""
        src, dest = _src_dest
        for use_nvenc in [True, False]:
            with patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg"):
                cmd = _build_video_cmd(src, dest, use_nvenc=use_nvenc)
            assert "-y" in cmd


class TestAnalyzeNvencFailureReason:
    def test_video_zero_kib_without_video_stream_is_audio_only(self) -> None:
        stderr = "video:0KiB audio:1777KiB"
        reason = _analyze_ffmpeg_failure_reason(stderr, has_video_stream=False)
        assert reason == "audio_only_input"

    def test_video_zero_kib_with_video_stream_is_decode_failure(self) -> None:
        stderr = "video:0KiB audio:1777KiB"
        reason = _analyze_ffmpeg_failure_reason(stderr, has_video_stream=True)
        assert reason == "cuda_decode_failed"
