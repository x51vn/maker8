"""Performance profiles that control rendering speed/quality trade-offs.

Each ``PerformanceMode`` maps to a ``PerfProfile`` that stages can query
for proxy resolution, FPS cap, encode presets, and effect policy.
"""

from __future__ import annotations

from dataclasses import dataclass

from maker8.models.common import PerformanceMode


@dataclass(frozen=True, slots=True)
class PerfProfile:
    """Concrete rendering parameters for a performance mode."""

    mode: PerformanceMode

    # Proxy: maximum short-edge resolution for pre-scaled video assets.
    # 0 means "no proxy, use source resolution".
    proxy_max_short_edge: int

    # FPS cap applied to the final encode (0 = use canvas fps).
    fps_cap: int

    # x264/NVENC encode preset – quality preset for the final video.
    encode_preset_cpu: str
    encode_preset_gpu: str

    # Whether to allow per-frame Python effects (slow path).
    # When False, effects that cannot be expressed as FFmpeg filters
    # are silently skipped.
    allow_python_effects: bool


# ── Built-in profiles ────────────────────────────────────────────────────────

_PROFILES: dict[PerformanceMode, PerfProfile] = {
    PerformanceMode.QUALITY: PerfProfile(
        mode=PerformanceMode.QUALITY,
        proxy_max_short_edge=0,  # no downscale – full source res
        fps_cap=0,  # use canvas fps
        encode_preset_cpu="slow",
        encode_preset_gpu="p6",  # higher quality NVENC
        allow_python_effects=True,
    ),
    PerformanceMode.BALANCED: PerfProfile(
        mode=PerformanceMode.BALANCED,
        proxy_max_short_edge=1080,  # scale to ≤1080 on short edge
        fps_cap=0,
        encode_preset_cpu="medium",
        encode_preset_gpu="p4",
        allow_python_effects=True,
    ),
    PerformanceMode.FAST: PerfProfile(
        mode=PerformanceMode.FAST,
        proxy_max_short_edge=720,  # aggressive downscale
        fps_cap=24,
        encode_preset_cpu="veryfast",
        encode_preset_gpu="p2",  # fastest NVENC
        allow_python_effects=False,
    ),
}


def get_profile(mode: str, proxy_override: int = 0) -> PerfProfile:
    """Resolve a ``PerfProfile`` from a mode string.

    Parameters
    ----------
    mode:
        One of ``"quality"``, ``"balanced"``, ``"fast"``.
    proxy_override:
        If > 0, overrides the profile's ``proxy_max_short_edge``.
    """
    try:
        perf_mode = PerformanceMode(mode)
    except ValueError:
        perf_mode = PerformanceMode.BALANCED

    profile = _PROFILES[perf_mode]

    if proxy_override > 0:
        return PerfProfile(
            mode=profile.mode,
            proxy_max_short_edge=proxy_override,
            fps_cap=profile.fps_cap,
            encode_preset_cpu=profile.encode_preset_cpu,
            encode_preset_gpu=profile.encode_preset_gpu,
            allow_python_effects=profile.allow_python_effects,
        )
    return profile
