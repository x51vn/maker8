"""Unified FFmpeg runtime resolution.

**Single source of truth** for the FFmpeg binary path used by every stage
in the pipeline — ``NORMALIZE``, ``RENDER``, and all capability probes.

Resolution order:

1. ``MAKER8_FFMPEG_PATH`` env var (explicit override)
2. ``IMAGEIO_FFMPEG_EXE`` env var (if already set — e.g. Dockerfile)
3. ``/usr/bin/ffmpeg`` on Linux if it exists (prefer system over bundled)
4. ``shutil.which("ffmpeg")`` on ``$PATH``

Using the bundled ``imageio_ffmpeg`` binary is deliberately **never**
attempted.  It ships without NVENC / hardware acceleration support and
has historically caused split-brain issues between normalize and render.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from maker8.utils.logging import get_logger

log = get_logger(__name__)

_SYSTEM_FFMPEG = "/usr/bin/ffmpeg"
_SYSTEM_FFPROBE = "/usr/bin/ffprobe"

_resolved_binary: str | None = None
_resolved_probe: str | None = None


def resolve_ffmpeg_binary() -> str:
    """Resolve the FFmpeg binary path.  Cached after first call."""
    global _resolved_binary  # noqa: PLW0603
    if _resolved_binary is not None:
        return _resolved_binary

    # 1. Explicit override
    explicit = os.environ.get("MAKER8_FFMPEG_PATH") or os.environ.get("IMAGEIO_FFMPEG_EXE")
    if explicit and Path(explicit).is_file():
        _resolved_binary = explicit
        return _resolved_binary

    # 2. Prefer system binary on Linux (apt-installed, has NVENC)
    if Path(_SYSTEM_FFMPEG).is_file():
        _resolved_binary = _SYSTEM_FFMPEG
        return _resolved_binary

    # 3. Fallback to $PATH
    which = shutil.which("ffmpeg")
    if which:
        _resolved_binary = which
        return _resolved_binary

    # Should not happen in a correctly built container
    _resolved_binary = "ffmpeg"
    return _resolved_binary


def resolve_ffprobe_binary() -> str:
    """Resolve the ffprobe path that matches the active FFmpeg runtime."""
    global _resolved_probe  # noqa: PLW0603
    if _resolved_probe is not None:
        return _resolved_probe

    sibling = Path(resolve_ffmpeg_binary()).with_name("ffprobe")
    if sibling.is_file():
        _resolved_probe = str(sibling)
        return _resolved_probe

    if Path(_SYSTEM_FFPROBE).is_file():
        _resolved_probe = _SYSTEM_FFPROBE
        return _resolved_probe

    which = shutil.which("ffprobe")
    if which:
        _resolved_probe = which
        return _resolved_probe

    _resolved_probe = "ffprobe"
    return _resolved_probe


def bind_moviepy_ffmpeg() -> None:
    """Force MoviePy / imageio-ffmpeg to use the unified binary.

    Sets ``IMAGEIO_FFMPEG_EXE`` so that ``imageio_ffmpeg.get_ffmpeg_exe()``
    returns the same path every stage uses.  Must be called early in
    ``app.main()`` **before** any MoviePy import triggers discovery.
    """
    ffmpeg = resolve_ffmpeg_binary()
    os.environ["IMAGEIO_FFMPEG_EXE"] = ffmpeg
    log.debug("ffmpeg_runtime.bind_moviepy", path=ffmpeg)


def get_ffmpeg_version(binary: str | None = None) -> str:
    """Return the version string of the resolved FFmpeg binary."""
    binary = binary or resolve_ffmpeg_binary()
    try:
        result = subprocess.run(
            [binary, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        first_line = result.stdout.split("\n", 1)[0]
        return first_line.strip()
    except Exception:
        return "unknown"


# ── Runtime diagnostics ──────────────────────────────────────────────────────


@dataclass
class FFmpegRuntimeInfo:
    """Startup diagnostics for the FFmpeg runtime."""

    system_ffmpeg_path: str
    render_ffmpeg_path: str
    same_binary: bool
    render_ffmpeg_version: str
    render_nvenc_available: bool
    imageio_ffmpeg_path: str


def diagnose_runtime() -> FFmpegRuntimeInfo:
    """Gather comprehensive FFmpeg runtime diagnostics.

    Checks the system FFmpeg, the render FFmpeg (what MoviePy will use),
    and whether they are the same binary.  Also verifies NVENC support
    on the **render** binary specifically.
    """
    system_path = shutil.which("ffmpeg") or "(not found)"
    render_path = resolve_ffmpeg_binary()

    # What imageio_ffmpeg would resolve to (for logging only)
    try:
        import imageio_ffmpeg

        imageio_path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        imageio_path = "(not installed)"

    # Check NVENC on the render binary
    nvenc = _probe_encoder(render_path, "h264_nvenc")

    return FFmpegRuntimeInfo(
        system_ffmpeg_path=system_path,
        render_ffmpeg_path=render_path,
        same_binary=os.path.realpath(system_path) == os.path.realpath(render_path),
        render_ffmpeg_version=get_ffmpeg_version(render_path),
        render_nvenc_available=nvenc,
        imageio_ffmpeg_path=imageio_path,
    )


def _probe_encoder(binary: str, encoder: str) -> bool:
    """Check if *binary* supports a given encoder."""
    try:
        result = subprocess.run(
            [binary, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return encoder in result.stdout
    except Exception:
        return False


def _reset_cache() -> None:
    """Reset the resolved binary cache — for testing only."""
    global _resolved_binary, _resolved_probe  # noqa: PLW0603
    _resolved_binary = None
    _resolved_probe = None
