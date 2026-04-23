"""Tests for GPU/CPU encoder detection and selection logic."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from maker8.rendering.encoder import (
    GpuCapabilities,
    _cpu_config,
    _gpu_config,
    _has_nvenc_encoder,
    _nvenc_smoke_test,
    check_nvenc,
    has_cuda_hwaccel,
    has_nvidia_smi,
    probe_gpu_capabilities,
    resolve_encoder,
)


@pytest.fixture(autouse=True)
def _reset_nvenc_cache() -> None:  # type: ignore[misc]
    """Clear the cached NVENC probe before and after each test."""
    import maker8.rendering.encoder as mod

    mod._NVENC_AVAILABLE = None
    yield  # type: ignore[misc]
    mod._NVENC_AVAILABLE = None


# ── _has_nvenc_encoder ───────────────────────────────────────────────────────


class TestHasNvencEncoder:
    def test_returns_true_when_encoder_listed(self) -> None:
        result = MagicMock(stdout="V..... h264_nvenc  NVIDIA NVENC H.264 encoder")
        with patch("maker8.rendering.encoder.subprocess.run", return_value=result):
            assert _has_nvenc_encoder() is True

    def test_returns_false_when_encoder_missing(self) -> None:
        result = MagicMock(stdout="V..... libx264  libx264 H.264")
        with patch("maker8.rendering.encoder.subprocess.run", return_value=result):
            assert _has_nvenc_encoder() is False

    def test_returns_false_on_exception(self) -> None:
        with patch(
            "maker8.rendering.encoder.subprocess.run",
            side_effect=FileNotFoundError("ffmpeg not found"),
        ):
            assert _has_nvenc_encoder() is False

    def test_returns_false_on_timeout(self) -> None:
        with patch(
            "maker8.rendering.encoder.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ffmpeg", 10),
        ):
            assert _has_nvenc_encoder() is False


# ── _nvenc_smoke_test ────────────────────────────────────────────────────────


class TestNvencSmokeTest:
    def test_returns_false_when_encoder_not_listed(self) -> None:
        with patch("maker8.rendering.encoder._has_nvenc_encoder", return_value=False):
            assert _nvenc_smoke_test() is False

    def test_returns_true_on_successful_encode(self, tmp_path: pytest.TempPathFactory) -> None:
        with (
            patch("maker8.rendering.encoder._has_nvenc_encoder", return_value=True),
            patch("maker8.rendering.encoder.tempfile.TemporaryDirectory") as mock_td,
        ):
            mock_td.return_value.__enter__ = MagicMock(return_value=str(tmp_path))
            mock_td.return_value.__exit__ = MagicMock(return_value=False)

            # Create fake output file to satisfy size check
            dest = tmp_path / "test_out.mp4"
            dest.write_bytes(b"\x00" * 200)

            result = MagicMock(returncode=0)
            with patch("maker8.rendering.encoder.subprocess.run", return_value=result):
                assert _nvenc_smoke_test() is True

    def test_returns_false_on_encode_failure(self, tmp_path: pytest.TempPathFactory) -> None:
        with (
            patch("maker8.rendering.encoder._has_nvenc_encoder", return_value=True),
            patch("maker8.rendering.encoder.tempfile.TemporaryDirectory") as mock_td,
        ):
            mock_td.return_value.__enter__ = MagicMock(return_value=str(tmp_path))
            mock_td.return_value.__exit__ = MagicMock(return_value=False)

            result = MagicMock(returncode=1, stderr="Encode failed")
            with patch("maker8.rendering.encoder.subprocess.run", return_value=result):
                assert _nvenc_smoke_test() is False

    def test_returns_false_on_timeout(self) -> None:
        with (
            patch("maker8.rendering.encoder._has_nvenc_encoder", return_value=True),
            patch(
                "maker8.rendering.encoder.subprocess.run",
                side_effect=subprocess.TimeoutExpired("ffmpeg", 15),
            ),
        ):
            assert _nvenc_smoke_test() is False

    def test_smoke_test_uses_256x256_resolution(self) -> None:
        """Ensure the smoke test uses frames large enough for NVENC (>= 256px)."""
        with (
            patch("maker8.rendering.encoder._has_nvenc_encoder", return_value=True),
            patch("maker8.rendering.encoder.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stderr="")
            _nvenc_smoke_test()
            # Inspect the FFmpeg command: it should encode 256x256, not 10x10
            cmd = mock_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            assert "256x256" in cmd_str
            assert "10x10" not in cmd_str

    def test_smoke_test_does_not_use_hwaccel_cuda_input(self) -> None:
        """Ensure lavfi input doesn't use -hwaccel cuda (not applicable)."""
        with (
            patch("maker8.rendering.encoder._has_nvenc_encoder", return_value=True),
            patch("maker8.rendering.encoder.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stderr="")
            _nvenc_smoke_test()
            cmd = mock_run.call_args[0][0]
            assert "-hwaccel" not in cmd


# ── check_nvenc (caching) ───────────────────────────────────────────────────


class TestCheckNvenc:
    def test_caches_result(self) -> None:
        with patch("maker8.rendering.encoder.has_nvenc", return_value=True) as mock:
            assert check_nvenc() is True
            assert check_nvenc() is True
            mock.assert_called_once()

    def test_caches_false_result(self) -> None:
        with patch("maker8.rendering.encoder.has_nvenc", return_value=False) as mock:
            assert check_nvenc() is False
            assert check_nvenc() is False
            mock.assert_called_once()


# ── resolve_encoder ──────────────────────────────────────────────────────────


class TestResolveEncoder:
    def test_auto_with_gpu(self) -> None:
        with patch("maker8.rendering.encoder.check_nvenc", return_value=True):
            cfg = resolve_encoder("auto", "medium")
            assert cfg.codec == "h264_nvenc"
            assert cfg.is_gpu is True

    def test_auto_without_gpu(self) -> None:
        with patch("maker8.rendering.encoder.check_nvenc", return_value=False):
            cfg = resolve_encoder("auto", "medium")
            assert cfg.codec == "libx264"
            assert cfg.is_gpu is False

    def test_libx264_upgrades_to_nvenc_when_available(self) -> None:
        with patch("maker8.rendering.encoder.check_nvenc", return_value=True):
            cfg = resolve_encoder("libx264", "medium")
            assert cfg.codec == "h264_nvenc"
            assert cfg.is_gpu is True

    def test_libx264_stays_cpu_when_no_gpu(self) -> None:
        with patch("maker8.rendering.encoder.check_nvenc", return_value=False):
            cfg = resolve_encoder("libx264", "medium")
            assert cfg.codec == "libx264"
            assert cfg.is_gpu is False
            assert cfg.preset == "medium"

    def test_h264_nvenc_honoured_when_available(self) -> None:
        with patch("maker8.rendering.encoder.check_nvenc", return_value=True):
            cfg = resolve_encoder("h264_nvenc", "p4")
            assert cfg.codec == "h264_nvenc"
            assert cfg.is_gpu is True

    def test_h264_nvenc_falls_back_when_unavailable(self) -> None:
        with patch("maker8.rendering.encoder.check_nvenc", return_value=False):
            cfg = resolve_encoder("h264_nvenc", "p4")
            assert cfg.codec == "libx264"
            assert cfg.is_gpu is False

    def test_explicit_cpu_codec_passes_through(self) -> None:
        cfg = resolve_encoder("libx265", "slow")
        assert cfg.codec == "libx265"
        assert cfg.preset == "slow"
        assert cfg.is_gpu is False

    def test_movflags_faststart_always_present(self) -> None:
        with patch("maker8.rendering.encoder.check_nvenc", return_value=True):
            gpu = resolve_encoder("auto", "medium")
            assert "+faststart" in " ".join(gpu.ffmpeg_params)

        with patch("maker8.rendering.encoder.check_nvenc", return_value=False):
            cpu = resolve_encoder("auto", "medium")
            assert "+faststart" in " ".join(cpu.ffmpeg_params)

    def test_auto_with_gpu_honours_preferred_gpu_preset(self) -> None:
        with patch("maker8.rendering.encoder.check_nvenc", return_value=True):
            cfg = resolve_encoder("auto", "medium", preferred_gpu_preset="p2")
            assert cfg.codec == "h264_nvenc"
            assert cfg.preset == "p2"
            assert cfg.is_gpu is True

    def test_auto_without_gpu_honours_preferred_cpu_preset(self) -> None:
        with patch("maker8.rendering.encoder.check_nvenc", return_value=False):
            cfg = resolve_encoder("auto", "medium", preferred_cpu_preset="veryfast")
            assert cfg.codec == "libx264"
            assert cfg.preset == "veryfast"
            assert cfg.is_gpu is False


# ── _cpu_config ──────────────────────────────────────────────────────────────


class TestCpuConfig:
    def test_normal_preset_preserved(self) -> None:
        cfg = _cpu_config("medium", "yuv420p")
        assert cfg.preset == "medium"
        assert cfg.codec == "libx264"
        assert cfg.is_gpu is False

    def test_nvenc_preset_sanitised_to_medium(self) -> None:
        for nvenc_preset in ("p1", "p2", "p3", "p4", "p5", "p6", "p7"):
            cfg = _cpu_config(nvenc_preset, "yuv420p")
            assert cfg.preset == "medium", f"NVENC preset {nvenc_preset} leaked to CPU encoder"
            assert cfg.codec == "libx264"

    def test_fast_preset_preserved(self) -> None:
        cfg = _cpu_config("fast", "yuv420p")
        assert cfg.preset == "fast"

    def test_pix_fmt_in_ffmpeg_params(self) -> None:
        cfg = _cpu_config("medium", "yuv420p")
        assert "-pix_fmt" in cfg.ffmpeg_params
        idx = cfg.ffmpeg_params.index("-pix_fmt")
        assert cfg.ffmpeg_params[idx + 1] == "yuv420p"


# ── _gpu_config ──────────────────────────────────────────────────────────────


class TestGpuConfig:
    def test_codec_is_nvenc(self) -> None:
        cfg = _gpu_config("yuv420p")
        assert cfg.codec == "h264_nvenc"
        assert cfg.is_gpu is True

    def test_preset_is_p4(self) -> None:
        cfg = _gpu_config("yuv420p")
        assert cfg.preset == "p4"

    def test_cq_param_present(self) -> None:
        cfg = _gpu_config("yuv420p")
        assert "-cq" in cfg.ffmpeg_params
        idx = cfg.ffmpeg_params.index("-cq")
        assert cfg.ffmpeg_params[idx + 1] == "23"


# ── GpuCapabilities ─────────────────────────────────────────────────────────


class TestGpuCapabilities:
    def test_gpu_render_enabled_when_nvenc_available(self) -> None:
        caps = GpuCapabilities(nvidia_smi=True, nvenc_available=True, cuda_hwaccel=True)
        assert caps.gpu_render_enabled is True

    def test_gpu_render_disabled_when_nvenc_unavailable(self) -> None:
        caps = GpuCapabilities(nvidia_smi=True, nvenc_available=False, cuda_hwaccel=True)
        assert caps.gpu_render_enabled is False

    def test_gpu_render_disabled_without_nvidia(self) -> None:
        caps = GpuCapabilities(nvidia_smi=False, nvenc_available=False, cuda_hwaccel=False)
        assert caps.gpu_render_enabled is False


# ── probe_gpu_capabilities ───────────────────────────────────────────────────


class TestProbeGpuCapabilities:
    def test_probes_all_capabilities(self) -> None:
        with (
            patch("maker8.rendering.encoder.has_nvidia_smi", return_value=True),
            patch("maker8.rendering.encoder.check_nvenc", return_value=True),
            patch("maker8.rendering.encoder.has_cuda_hwaccel", return_value=True),
        ):
            caps = probe_gpu_capabilities()
            assert caps.nvidia_smi is True
            assert caps.nvenc_available is True
            assert caps.cuda_hwaccel is True
            assert caps.gpu_render_enabled is True

    def test_cpu_only_machine(self) -> None:
        with (
            patch("maker8.rendering.encoder.has_nvidia_smi", return_value=False),
            patch("maker8.rendering.encoder.check_nvenc", return_value=False),
            patch("maker8.rendering.encoder.has_cuda_hwaccel", return_value=False),
        ):
            caps = probe_gpu_capabilities()
            assert caps.nvidia_smi is False
            assert caps.nvenc_available is False
            assert caps.cuda_hwaccel is False
            assert caps.gpu_render_enabled is False


# ── has_nvidia_smi ───────────────────────────────────────────────────────────


class TestHasNvidiaSmi:
    def test_returns_true_when_smi_succeeds(self) -> None:
        result = MagicMock(returncode=0, stdout="NVIDIA RTX 3060,535.183.01")
        with patch("maker8.rendering.encoder.subprocess.run", return_value=result):
            assert has_nvidia_smi() is True

    def test_returns_false_when_smi_fails(self) -> None:
        result = MagicMock(returncode=1)
        with patch("maker8.rendering.encoder.subprocess.run", return_value=result):
            assert has_nvidia_smi() is False

    def test_returns_false_when_smi_not_found(self) -> None:
        with patch(
            "maker8.rendering.encoder.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert has_nvidia_smi() is False


# ── has_cuda_hwaccel ─────────────────────────────────────────────────────────


class TestHasCudaHwaccel:
    def test_returns_true_when_cuda_listed(self) -> None:
        result = MagicMock(stdout="Hardware acceleration methods:\ncuda\nvdpau\n")
        with patch("maker8.rendering.encoder.subprocess.run", return_value=result):
            assert has_cuda_hwaccel() is True

    def test_returns_false_when_cuda_not_listed(self) -> None:
        result = MagicMock(stdout="Hardware acceleration methods:\nvdpau\n")
        with patch("maker8.rendering.encoder.subprocess.run", return_value=result):
            assert has_cuda_hwaccel() is False

    def test_returns_false_on_exception(self) -> None:
        with patch(
            "maker8.rendering.encoder.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert has_cuda_hwaccel() is False
