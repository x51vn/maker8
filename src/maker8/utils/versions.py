"""Engine version detection – shared by orchestrator, upload, and emit stages."""

from __future__ import annotations

import subprocess

from maker8.models.common import EngineVersions


def collect_engine_versions() -> EngineVersions:
    """Detect installed versions of MoviePy, FFmpeg, and yt-dlp."""
    versions = EngineVersions()

    try:
        import moviepy

        versions.moviepy = getattr(moviepy, "__version__", "unknown")
    except ImportError:
        pass

    try:
        out = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        parts = out.stdout.split("\n", 1)[0].split(" ")
        versions.ffmpeg = parts[2] if len(parts) > 2 else parts[0]
    except Exception:
        pass

    try:
        out = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        versions.youtube_dlp = out.stdout.strip()
    except Exception:
        pass

    return versions
