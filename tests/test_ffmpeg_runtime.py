"""Tests for the unified FFmpeg runtime resolution."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from maker8.rendering.ffmpeg_runtime import (
    FFmpegRuntimeInfo,
    _reset_cache,
    bind_moviepy_ffmpeg,
    diagnose_runtime,
    get_ffmpeg_version,
    resolve_ffmpeg_binary,
    resolve_ffprobe_binary,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:  # type: ignore[misc]
    """Reset the cached binary before and after every test."""
    _reset_cache()
    yield  # type: ignore[misc]
    _reset_cache()


class TestResolveFFmpegBinary:
    """Tests for resolve_ffmpeg_binary()."""

    def test_explicit_maker8_env_override(self, tmp_path: Path) -> None:
        fake = tmp_path / "ffmpeg"
        fake.write_text("#!/bin/sh\n")
        with patch.dict(os.environ, {"MAKER8_FFMPEG_PATH": str(fake)}):
            assert resolve_ffmpeg_binary() == str(fake)

    def test_explicit_imageio_env_override(self, tmp_path: Path) -> None:
        fake = tmp_path / "ffmpeg"
        fake.write_text("#!/bin/sh\n")
        env = {"IMAGEIO_FFMPEG_EXE": str(fake)}
        with patch.dict(os.environ, env, clear=False):
            # Remove MAKER8_FFMPEG_PATH if present
            os.environ.pop("MAKER8_FFMPEG_PATH", None)
            assert resolve_ffmpeg_binary() == str(fake)

    def test_maker8_env_takes_precedence(self, tmp_path: Path) -> None:
        f1 = tmp_path / "ffmpeg_maker8"
        f1.write_text("#!/bin/sh\n")
        f2 = tmp_path / "ffmpeg_imageio"
        f2.write_text("#!/bin/sh\n")
        env = {
            "MAKER8_FFMPEG_PATH": str(f1),
            "IMAGEIO_FFMPEG_EXE": str(f2),
        }
        with patch.dict(os.environ, env):
            assert resolve_ffmpeg_binary() == str(f1)

    def test_system_binary_fallback(self) -> None:
        """When no env vars are set, prefer /usr/bin/ffmpeg if it exists."""
        env_clear = {"MAKER8_FFMPEG_PATH": "", "IMAGEIO_FFMPEG_EXE": ""}
        with patch.dict(os.environ, env_clear):
            if Path("/usr/bin/ffmpeg").is_file():
                assert resolve_ffmpeg_binary() == "/usr/bin/ffmpeg"
            else:
                # Falls through to shutil.which
                result = resolve_ffmpeg_binary()
                assert result  # should find something

    def test_which_fallback(self) -> None:
        """When system path doesn't exist, falls back to shutil.which."""
        env_clear = {"MAKER8_FFMPEG_PATH": "", "IMAGEIO_FFMPEG_EXE": ""}
        with (
            patch.dict(os.environ, env_clear),
            patch("maker8.rendering.ffmpeg_runtime.Path") as mock_path_cls,
            patch.object(shutil, "which", return_value="/opt/ffmpeg/bin/ffmpeg"),
        ):
            # Make Path("/usr/bin/ffmpeg").is_file() return False
            mock_path_cls.return_value.is_file.return_value = False
            result = resolve_ffmpeg_binary()
            assert result == "/opt/ffmpeg/bin/ffmpeg"

    def test_caching(self) -> None:
        """Second call returns cached value without re-probing."""
        first = resolve_ffmpeg_binary()
        second = resolve_ffmpeg_binary()
        assert first == second


class TestResolveFFprobeBinary:
    """Tests for resolve_ffprobe_binary()."""

    def test_prefers_sibling_of_explicit_ffmpeg(self, tmp_path: Path) -> None:
        bindir = tmp_path / "bin"
        bindir.mkdir()
        fake_ffmpeg = bindir / "custom-ffmpeg"
        fake_ffprobe = bindir / "ffprobe"
        fake_ffmpeg.write_text("#!/bin/sh\n")
        fake_ffprobe.write_text("#!/bin/sh\n")

        with patch.dict(os.environ, {"MAKER8_FFMPEG_PATH": str(fake_ffmpeg)}):
            assert resolve_ffprobe_binary() == str(fake_ffprobe)

    def test_falls_back_to_path_when_no_sibling_exists(self) -> None:
        with (
            patch(
                "maker8.rendering.ffmpeg_runtime.resolve_ffmpeg_binary",
                return_value="/opt/bin/ffmpeg",
            ),
            patch("maker8.rendering.ffmpeg_runtime.Path") as mock_path_cls,
            patch.object(shutil, "which", return_value="/usr/local/bin/ffprobe"),
        ):
            path_instance = mock_path_cls.return_value
            path_instance.with_name.return_value.is_file.return_value = False
            path_instance.is_file.return_value = False
            assert resolve_ffprobe_binary() == "/usr/local/bin/ffprobe"


class TestBindMoviepyFfmpeg:
    """Tests for bind_moviepy_ffmpeg()."""

    def test_sets_imageio_env(self) -> None:
        bind_moviepy_ffmpeg()
        assert os.environ.get("IMAGEIO_FFMPEG_EXE") == resolve_ffmpeg_binary()


class TestGetFfmpegVersion:
    """Tests for get_ffmpeg_version()."""

    def test_returns_version_string(self) -> None:
        binary = resolve_ffmpeg_binary()
        version = get_ffmpeg_version(binary)
        # Should be a real version string from ffmpeg
        assert "ffmpeg" in version.lower() or version == "unknown"

    def test_nonexistent_returns_unknown(self) -> None:
        assert get_ffmpeg_version("/nonexistent/ffmpeg") == "unknown"


class TestDiagnoseRuntime:
    """Tests for diagnose_runtime()."""

    def test_returns_runtime_info(self) -> None:
        info = diagnose_runtime()
        assert isinstance(info, FFmpegRuntimeInfo)
        assert info.render_ffmpeg_path
        assert info.render_ffmpeg_version != ""

    def test_same_binary_flag(self) -> None:
        info = diagnose_runtime()
        # If system ffmpeg exists, render should use it — hence same_binary=True
        if shutil.which("ffmpeg"):
            assert info.same_binary is True
