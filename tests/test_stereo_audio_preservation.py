"""Unit tests for stereo audio preservation in NormalizeStage.

Covers _probe_audio_channels and the updated _normalize_audio method.
All ffprobe/ffmpeg subprocess calls are mocked — no real media files needed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from maker8.pipeline.normalize import NormalizeStage, _probe_audio_channels

# ── Helpers ──────────────────────────────────────────────────────────────────

_FAKE_SRC = Path("/tmp/fake_audio.mp3")
_FAKE_DEST_DIR = Path("/tmp/norm")


def _make_completed(stdout: str = "", returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


# ── _probe_audio_channels ─────────────────────────────────────────────────────


@patch("maker8.pipeline.normalize.resolve_ffprobe_binary", return_value="ffprobe")
@patch("subprocess.run")
def test_probe_returns_2_for_stereo(mock_run, _mock_ffprobe):
    """(a) ffprobe returning '2' → 2 channels."""
    mock_run.return_value = _make_completed("2\n")
    assert _probe_audio_channels(_FAKE_SRC) == 2


@patch("maker8.pipeline.normalize.resolve_ffprobe_binary", return_value="ffprobe")
@patch("subprocess.run")
def test_probe_returns_1_for_mono(mock_run, _mock_ffprobe):
    """(b) ffprobe returning '1' → 1 channel."""
    mock_run.return_value = _make_completed("1\n")
    assert _probe_audio_channels(_FAKE_SRC) == 1


@patch("maker8.pipeline.normalize.resolve_ffprobe_binary", return_value="ffprobe")
@patch("subprocess.run")
def test_probe_timeout_falls_back_to_mono(mock_run, _mock_ffprobe):
    """(c) TimeoutExpired → fallback 1."""
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffprobe", timeout=10)
    assert _probe_audio_channels(_FAKE_SRC) == 1


@patch("maker8.pipeline.normalize.resolve_ffprobe_binary", return_value="ffprobe")
@patch("subprocess.run")
def test_probe_non_numeric_output_falls_back_to_mono(mock_run, _mock_ffprobe):
    """(d) Non-numeric stdout → fallback 1."""
    mock_run.return_value = _make_completed("N/A\n")
    assert _probe_audio_channels(_FAKE_SRC) == 1


# ── _normalize_audio ─────────────────────────────────────────────────────────


def _run_normalize_audio(src: Path, max_channels: int) -> list[str]:
    """Run _normalize_audio with mocked ffmpeg/ffprobe and return the cmd used."""
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        m = MagicMock()
        m.returncode = 0
        return m

    dest = _FAKE_DEST_DIR / f"{src.stem}_norm.wav"

    with (
        patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg"),
        patch("maker8.pipeline.normalize.resolve_ffprobe_binary", return_value="ffprobe"),
        patch("maker8.pipeline.normalize._is_valid_media", return_value=False),
        patch("maker8.pipeline.normalize._probe_audio_channels", return_value=2) as mock_probe,
        patch.object(Path, "unlink"),
        patch("subprocess.run", side_effect=fake_run),
        patch("maker8.pipeline.normalize.Timer") as mock_timer,
        patch("maker8.pipeline.normalize.SUBPROCESS_DURATION"),
        patch("maker8.pipeline.normalize.SUBPROCESS_FAILURES"),
    ):
        mock_timer.return_value.start.return_value = mock_timer.return_value
        mock_probe.return_value = 2  # default: stereo source
        NormalizeStage._normalize_audio(src, _FAKE_DEST_DIR, "job-1", "asset-1", max_channels)

    # Return the FFmpeg command (last call — ffprobe calls come first)
    ffmpeg_calls = [c for c in captured if c[0] == "ffmpeg"]
    return ffmpeg_calls[-1] if ffmpeg_calls else []


@patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg")
@patch("maker8.pipeline.normalize.resolve_ffprobe_binary", return_value="ffprobe")
@patch("maker8.pipeline.normalize._is_valid_media", return_value=False)
@patch("maker8.pipeline.normalize._probe_audio_channels", return_value=2)
@patch("maker8.pipeline.normalize.Timer")
@patch("maker8.pipeline.normalize.SUBPROCESS_DURATION")
@patch("maker8.pipeline.normalize.SUBPROCESS_FAILURES")
@patch("subprocess.run")
@patch.object(Path, "unlink")
def test_stereo_source_default_max_uses_ac2(
    mock_unlink, mock_run, _sf, _sd, mock_timer, mock_probe, _iv, _ffprobe, _ffmpeg
):
    """(e) Stereo source + default max (2) → -ac 2."""
    mock_run.return_value = MagicMock(returncode=0)
    mock_timer.return_value.start.return_value = mock_timer.return_value
    NormalizeStage._normalize_audio(_FAKE_SRC, _FAKE_DEST_DIR, "j", "a", max_audio_channels=2)
    ffmpeg_cmd = mock_run.call_args[0][0]
    ac_idx = ffmpeg_cmd.index("-ac")
    assert ffmpeg_cmd[ac_idx + 1] == "2"


@patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg")
@patch("maker8.pipeline.normalize.resolve_ffprobe_binary", return_value="ffprobe")
@patch("maker8.pipeline.normalize._is_valid_media", return_value=False)
@patch("maker8.pipeline.normalize._probe_audio_channels", return_value=2)
@patch("maker8.pipeline.normalize.Timer")
@patch("maker8.pipeline.normalize.SUBPROCESS_DURATION")
@patch("maker8.pipeline.normalize.SUBPROCESS_FAILURES")
@patch("subprocess.run")
@patch.object(Path, "unlink")
def test_stereo_source_max1_forces_mono(
    mock_unlink, mock_run, _sf, _sd, mock_timer, mock_probe, _iv, _ffprobe, _ffmpeg
):
    """(f) Stereo source + max_audio_channels=1 → -ac 1 (legacy mono)."""
    mock_run.return_value = MagicMock(returncode=0)
    mock_timer.return_value.start.return_value = mock_timer.return_value
    NormalizeStage._normalize_audio(_FAKE_SRC, _FAKE_DEST_DIR, "j", "a", max_audio_channels=1)
    ffmpeg_cmd = mock_run.call_args[0][0]
    ac_idx = ffmpeg_cmd.index("-ac")
    assert ffmpeg_cmd[ac_idx + 1] == "1"


@patch("maker8.pipeline.normalize.resolve_ffmpeg_binary", return_value="ffmpeg")
@patch("maker8.pipeline.normalize.resolve_ffprobe_binary", return_value="ffprobe")
@patch("maker8.pipeline.normalize._is_valid_media", return_value=False)
@patch("maker8.pipeline.normalize._probe_audio_channels", return_value=6)
@patch("maker8.pipeline.normalize.Timer")
@patch("maker8.pipeline.normalize.SUBPROCESS_DURATION")
@patch("maker8.pipeline.normalize.SUBPROCESS_FAILURES")
@patch("subprocess.run")
@patch.object(Path, "unlink")
def test_surround_source_clamped_to_2(
    mock_unlink, mock_run, _sf, _sd, mock_timer, mock_probe, _iv, _ffprobe, _ffmpeg
):
    """(g) 6-channel source + default max (2) → clamped to -ac 2."""
    mock_run.return_value = MagicMock(returncode=0)
    mock_timer.return_value.start.return_value = mock_timer.return_value
    NormalizeStage._normalize_audio(_FAKE_SRC, _FAKE_DEST_DIR, "j", "a", max_audio_channels=2)
    ffmpeg_cmd = mock_run.call_args[0][0]
    ac_idx = ffmpeg_cmd.index("-ac")
    assert ffmpeg_cmd[ac_idx + 1] == "2"
