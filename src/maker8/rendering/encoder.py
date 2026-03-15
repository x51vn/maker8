"""GPU/CPU encoder detection and selection for FFmpeg-based rendering.

Shared by both the ``NORMALIZE`` and ``RENDER`` stages.  Probes NVENC
availability once and caches the result for the process lifetime.

All FFmpeg invocations use the binary resolved by
:func:`~maker8.rendering.ffmpeg_runtime.resolve_ffmpeg_binary` to
guarantee a single runtime across the entire pipeline.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from maker8.rendering.ffmpeg_runtime import resolve_ffmpeg_binary
from maker8.utils.logging import get_logger

log = get_logger(__name__)

# ── Capability detection ─────────────────────────────────────────────────────

_NVENC_AVAILABLE: bool | None = None


def has_nvenc() -> bool:
    """Return ``True`` if ``h264_nvenc`` is listed in FFmpeg encoders."""
    ffmpeg = resolve_ffmpeg_binary()
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "h264_nvenc" in result.stdout
    except Exception:
        return False


def check_nvenc() -> bool:
    """Cached NVENC availability — probes only once per process."""
    global _NVENC_AVAILABLE  # noqa: PLW0603
    if _NVENC_AVAILABLE is None:
        _NVENC_AVAILABLE = has_nvenc()
        log.info(
            "gpu.nvenc_probe",
            nvenc_available=_NVENC_AVAILABLE,
            ffmpeg_binary=resolve_ffmpeg_binary(),
        )
    return _NVENC_AVAILABLE


def has_nvidia_smi() -> bool:
    """Return ``True`` if ``nvidia-smi`` succeeds."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def has_cuda_hwaccel() -> bool:
    """Return ``True`` if FFmpeg exposes CUDA hardware acceleration."""
    ffmpeg = resolve_ffmpeg_binary()
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "cuda" in result.stdout
    except Exception:
        return False


@dataclass
class GpuCapabilities:
    """Startup summary of GPU capabilities."""

    nvidia_smi: bool
    nvenc_available: bool
    cuda_hwaccel: bool

    @property
    def gpu_render_enabled(self) -> bool:
        return self.nvenc_available


def probe_gpu_capabilities() -> GpuCapabilities:
    """Probe all GPU capabilities once at startup."""
    return GpuCapabilities(
        nvidia_smi=has_nvidia_smi(),
        nvenc_available=check_nvenc(),
        cuda_hwaccel=has_cuda_hwaccel(),
    )


# ── Encoder configuration ───────────────────────────────────────────────────


@dataclass
class EncoderConfig:
    """Resolved encoder settings for ``write_videofile``."""

    codec: str
    preset: str
    ffmpeg_params: list[str]
    is_gpu: bool


def resolve_encoder(
    requested_codec: str,
    requested_preset: str,
    pix_fmt: str = "yuv420p",
) -> EncoderConfig:
    """Resolve the final encoder configuration.

    Rules:

    - ``"auto"`` or ``"libx264"`` → ``h264_nvenc`` when available, else ``libx264``
    - ``"h264_nvenc"`` → honour if available, else warn + fallback
    - anything else → honour as-is

    ``"libx264"`` is treated as upgradeable because it is the historical
    default.  Requests generated before the ``"auto"`` contract change
    carry ``"libx264"`` but should still benefit from GPU acceleration.
    """
    if requested_codec in ("auto", "libx264"):
        if check_nvenc():
            return _gpu_config(pix_fmt)
        return _cpu_config(requested_preset, pix_fmt)

    if requested_codec == "h264_nvenc":
        if check_nvenc():
            return _gpu_config(pix_fmt)
        log.warning("encoder.nvenc_requested_but_unavailable", fallback="libx264")
        return _cpu_config(requested_preset, pix_fmt)

    # Explicit CPU codec — honour as-is
    return EncoderConfig(
        codec=requested_codec,
        preset=requested_preset,
        ffmpeg_params=["-pix_fmt", pix_fmt, "-movflags", "+faststart"],
        is_gpu=False,
    )


def _gpu_config(pix_fmt: str) -> EncoderConfig:
    return EncoderConfig(
        codec="h264_nvenc",
        preset="p4",
        ffmpeg_params=[
            "-pix_fmt",
            pix_fmt,
            "-cq",
            "23",
            "-movflags",
            "+faststart",
        ],
        is_gpu=True,
    )


def _cpu_config(preset: str, pix_fmt: str) -> EncoderConfig:
    # Guard against NVENC preset leaking into CPU encoder
    cpu_preset = preset if preset not in ("p1", "p2", "p3", "p4", "p5", "p6", "p7") else "medium"
    return EncoderConfig(
        codec="libx264",
        preset=cpu_preset,
        ffmpeg_params=["-pix_fmt", pix_fmt, "-movflags", "+faststart"],
        is_gpu=False,
    )
