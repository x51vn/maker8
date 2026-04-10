"""Regression tests for YouTube format=null normalization and resolve error classification.

Covers:
  - format=None → default (no failure)
  - format omitted → default (no failure)
  - format="" → explicit validation error, non-retryable
  - format="mp4" → passes through
  - resolve.py classifies ValueError as non-retryable with specific codes
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ── Stub prometheus_client before any maker8 module that uses metrics ────────
_prom_stub = ModuleType("prometheus_client")
for _attr in ("Counter", "Gauge", "Histogram", "Summary", "start_http_server"):
    setattr(_prom_stub, _attr, MagicMock())
sys.modules.setdefault("prometheus_client", _prom_stub)

from maker8.pipeline.resolve import ResolveAssetsStage, _classify_value_error  # noqa: E402
from maker8.plugins.sources.youtube import (  # noqa: E402
    _DEFAULT_FORMAT,
    YouTubeRuntimeConfig,
    YouTubeSourceConnector,
    YtdlpError,
    _build_base_cmd,
    _build_download_cmd,
    _build_resolve_cmd,
    _extract_stderr_summary,
    classify_ytdlp_stderr,
    probe_ytdlp,
    resolve_ytdlp_path,
)
from maker8.retry import StageError  # noqa: E402

# ── YouTubeSourceConnector.resolve format handling ───────────────────────────


class TestYouTubeFormatNormalization:
    """Verify that None/missing format is normalized to the default."""

    def _make_source(self, *, url: str = "https://youtu.be/test", **opts: object) -> dict:
        src: dict = {"url": url, "options": {}}
        src["options"].update(opts)
        return src

    @patch("maker8.plugins.sources.youtube.subprocess.run")
    def test_format_none_uses_default(self, mock_run: MagicMock) -> None:
        """format=None must be treated as 'use default', not fail."""
        mock_run.return_value = MagicMock(
            stdout='{"title":"t","duration":10,"ext":"mp4"}',
        )
        connector = YouTubeSourceConnector()
        source = self._make_source(format=None)
        plan = connector.resolve("yt_test", source)

        assert plan.format_spec == _DEFAULT_FORMAT
        cmd = mock_run.call_args[0][0]
        assert _DEFAULT_FORMAT in cmd

    @patch("maker8.plugins.sources.youtube.subprocess.run")
    def test_format_omitted_uses_default(self, mock_run: MagicMock) -> None:
        """No format key at all must use the default."""
        mock_run.return_value = MagicMock(
            stdout='{"title":"t","duration":10,"ext":"mp4"}',
        )
        connector = YouTubeSourceConnector()
        source = {"url": "https://youtu.be/test", "options": {}}
        plan = connector.resolve("yt_test", source)

        assert plan.format_spec == _DEFAULT_FORMAT

    @patch("maker8.plugins.sources.youtube.subprocess.run")
    def test_format_explicit_passes_through(self, mock_run: MagicMock) -> None:
        """An explicit format must be used as-is."""
        mock_run.return_value = MagicMock(
            stdout='{"title":"t","duration":10,"ext":"mp4"}',
        )
        connector = YouTubeSourceConnector()
        source = self._make_source(format="mp4")
        plan = connector.resolve("yt_test", source)

        assert plan.format_spec == "mp4"
        cmd = mock_run.call_args[0][0]
        assert "mp4" in cmd

    def test_format_empty_string_raises(self) -> None:
        """Empty string format must raise a clear ValueError."""
        connector = YouTubeSourceConnector()
        source = self._make_source(format="")
        with pytest.raises(ValueError, match="Empty yt-dlp format spec"):
            connector.resolve("yt_test", source)

    def test_format_whitespace_only_raises(self) -> None:
        """Whitespace-only format must also fail explicitly."""
        connector = YouTubeSourceConnector()
        source = self._make_source(format="   ")
        with pytest.raises(ValueError, match="Empty yt-dlp format spec"):
            connector.resolve("yt_test", source)

    def test_missing_url_raises(self) -> None:
        """Missing URL must raise ValueError."""
        connector = YouTubeSourceConnector()
        source: dict = {"options": {}}
        with pytest.raises(ValueError, match="no 'url'"):
            connector.resolve("yt_test", source)


# ── Error classification ─────────────────────────────────────────────────────


class TestClassifyValueError:
    """Verify _classify_value_error maps messages to correct codes."""

    def test_url_error(self) -> None:
        assert _classify_value_error("no 'url' in source") == "INVALID_SOURCE_URL"

    def test_format_error(self) -> None:
        assert _classify_value_error("Empty yt-dlp format spec") == "INVALID_YTDLP_FORMAT"

    def test_duration_error(self) -> None:
        assert _classify_value_error("duration 300s exceeds max") == "INVALID_SOURCE_OPTIONS"

    def test_generic_error(self) -> None:
        assert _classify_value_error("something unexpected") == "INVALID_SOURCE_CONFIG"


# ── ResolveAssetsStage error retryability ────────────────────────────────────


class TestResolveNonRetryableErrors:
    """Verify that deterministic errors from resolve are marked non-retryable."""

    def _make_ctx(self, assets: list[dict]) -> MagicMock:
        """Build a minimal mock PipelineContext."""

        ctx = MagicMock()
        ctx.job_id = "test-job"
        ctx.resolved_plans = {}

        # Build real Asset models
        from render_contracts.render_spec import Asset

        ctx.render_spec.assets = [Asset.model_validate(a) for a in assets]
        return ctx

    def test_empty_url_is_non_retryable(self) -> None:
        """Missing URL should yield INVALID_SOURCE_URL, non-retryable."""
        registry = MagicMock()
        connector = YouTubeSourceConnector()
        registry.get_source.return_value = connector

        stage = ResolveAssetsStage(registry)
        ctx = self._make_ctx(
            [
                {
                    "id": "yt_bad",
                    "type": "video",
                    "source": {"kind": "youtube", "url": "", "options": {}},
                }
            ]
        )

        with pytest.raises(StageError) as exc_info:
            stage.execute(ctx)
        assert exc_info.value.code == "INVALID_SOURCE_URL"
        assert exc_info.value.retryable is False

    def test_empty_format_is_non_retryable(self) -> None:
        """Empty format string should yield INVALID_YTDLP_FORMAT, non-retryable."""
        registry = MagicMock()
        connector = YouTubeSourceConnector()
        registry.get_source.return_value = connector

        stage = ResolveAssetsStage(registry)
        ctx = self._make_ctx(
            [
                {
                    "id": "yt_bad",
                    "type": "video",
                    "source": {
                        "kind": "youtube",
                        "url": "https://youtu.be/test",
                        "options": {"format": ""},
                    },
                }
            ]
        )

        with pytest.raises(StageError) as exc_info:
            stage.execute(ctx)
        assert exc_info.value.code == "INVALID_YTDLP_FORMAT"
        assert exc_info.value.retryable is False

    def test_unsupported_source_is_non_retryable(self) -> None:
        """Unknown source kind should be UNSUPPORTED_SOURCE, non-retryable."""
        registry = MagicMock()
        registry.get_source.side_effect = KeyError("badkind")

        stage = ResolveAssetsStage(registry)
        ctx = self._make_ctx(
            [
                {
                    "id": "asset_bad",
                    "type": "video",
                    "source": {"kind": "badkind", "url": "http://x", "options": {}},
                }
            ]
        )

        with pytest.raises(StageError) as exc_info:
            stage.execute(ctx)
        assert exc_info.value.code == "UNSUPPORTED_SOURCE"
        assert exc_info.value.retryable is False

    @patch("maker8.plugins.sources.youtube.subprocess.run")
    def test_format_null_resolves_successfully(self, mock_run: MagicMock) -> None:
        """format=null should normalize to default and resolve successfully."""
        mock_run.return_value = MagicMock(
            stdout='{"title":"t","duration":10,"ext":"mp4"}',
        )
        registry = MagicMock()
        connector = YouTubeSourceConnector()
        registry.get_source.return_value = connector

        stage = ResolveAssetsStage(registry)
        ctx = self._make_ctx(
            [
                {
                    "id": "yt_null_fmt",
                    "type": "video",
                    "source": {
                        "kind": "youtube",
                        "url": "https://youtu.be/test",
                        "options": {"format": None},
                    },
                }
            ]
        )

        stage.execute(ctx)  # Should not raise
        assert "yt_null_fmt" in ctx.resolved_plans
        assert ctx.resolved_plans["yt_null_fmt"].format_spec == _DEFAULT_FORMAT


# ── Error classification ─────────────────────────────────────────────────────


class TestClassifyYtdlpStderr:
    """Verify classify_ytdlp_stderr maps stderr to (code, retryable)."""

    @pytest.mark.parametrize(
        ("stderr", "expected_code", "expected_retryable"),
        [
            ("ERROR: Video unavailable", "YTDLP_VIDEO_UNAVAILABLE", False),
            ("ERROR: Private video", "YTDLP_VIDEO_UNAVAILABLE", False),
            ("ERROR: This video is private", "YTDLP_VIDEO_UNAVAILABLE", False),
            ("ERROR: This video is unavailable", "YTDLP_VIDEO_UNAVAILABLE", False),
            ("ERROR: This video has been removed", "YTDLP_VIDEO_UNAVAILABLE", False),
            ("Requested format is not available", "YTDLP_FORMAT_UNAVAILABLE", False),
            ("ERROR: Unsupported URL: xyz", "YTDLP_UNSUPPORTED_URL", False),
            ("Sign in to confirm you're not a bot", "YTDLP_AUTH_REQUIRED", False),
            ("ERROR: Login required", "YTDLP_AUTH_REQUIRED", False),
            ("This content is age-restricted", "YTDLP_AUTH_REQUIRED", False),
            ("Join this channel to get access", "YTDLP_AUTH_REQUIRED", False),
            ("PO Token required", "YTDLP_PO_TOKEN_REQUIRED", False),
            ("po_token is needed", "YTDLP_PO_TOKEN_REQUIRED", False),
            ("HTTP Error 429: Too Many Requests", "YTDLP_RATE_LIMITED", True),
            ("HTTP Error 503", "YTDLP_SERVER_ERROR", True),
            ("HTTP Error 500", "YTDLP_SERVER_ERROR", True),
            ("Connection timed out", "YTDLP_NETWORK_FAILURE", True),
            ("Connection reset by peer", "YTDLP_NETWORK_FAILURE", True),
            ("SSL: CERTIFICATE_VERIFY_FAILED", "YTDLP_NETWORK_FAILURE", True),
            ("Name resolution failed (dns)", "YTDLP_NETWORK_FAILURE", True),
            ("Temporary failure in name resolution", "YTDLP_NETWORK_FAILURE", True),
        ],
    )
    def test_known_patterns(
        self,
        stderr: str,
        expected_code: str,
        expected_retryable: bool,
    ) -> None:
        code, retryable = classify_ytdlp_stderr(stderr)
        assert code == expected_code
        assert retryable is expected_retryable

    def test_unknown_stderr_is_retryable(self) -> None:
        code, retryable = classify_ytdlp_stderr("some unknown weird error")
        assert code == "YTDLP_EXTRACTOR_FAILURE"
        assert retryable is True

    def test_empty_stderr(self) -> None:
        code, retryable = classify_ytdlp_stderr("")
        assert code == "YTDLP_EXTRACTOR_FAILURE"
        assert retryable is True


class TestExtractStderrSummary:
    """Verify _extract_stderr_summary extracts the right line."""

    def test_error_line_extracted(self) -> None:
        stderr = "WARNING: something\nERROR: Video unavailable\n"
        assert _extract_stderr_summary(stderr) == "ERROR: Video unavailable"

    def test_last_error_line_wins(self) -> None:
        stderr = "ERROR: First error\nWARNING: warning\nERROR: Sign in to confirm"
        assert _extract_stderr_summary(stderr) == "ERROR: Sign in to confirm"

    def test_no_error_line_returns_last(self) -> None:
        stderr = "WARNING: something\nsome fallback line"
        assert _extract_stderr_summary(stderr) == "some fallback line"

    def test_empty_returns_placeholder(self) -> None:
        assert _extract_stderr_summary("") == "(no stderr)"


# ── Command builder ──────────────────────────────────────────────────────────


class TestCommandBuilder:
    """Verify _build_*_cmd functions produce correct command lists."""

    def test_base_cmd_defaults(self) -> None:
        cfg = YouTubeRuntimeConfig()
        cmd = _build_base_cmd(cfg)
        assert cmd == ["yt-dlp"]

    def test_base_cmd_with_cookies_file(self) -> None:
        cfg = YouTubeRuntimeConfig(cookies_file="/tmp/cookies.txt")
        cmd = _build_base_cmd(cfg)
        assert "--cookies" in cmd
        assert "/tmp/cookies.txt" in cmd

    def test_base_cmd_with_cookies_browser(self) -> None:
        cfg = YouTubeRuntimeConfig(cookies_from_browser="chrome")
        cmd = _build_base_cmd(cfg)
        assert "--cookies-from-browser" in cmd
        assert "chrome" in cmd

    def test_cookies_file_takes_precedence(self) -> None:
        """When both are set, cookies_file wins."""
        cfg = YouTubeRuntimeConfig(
            cookies_file="/tmp/cookies.txt",
            cookies_from_browser="chrome",
        )
        cmd = _build_base_cmd(cfg)
        assert "--cookies" in cmd
        assert "--cookies-from-browser" not in cmd

    def test_base_cmd_with_user_agent(self) -> None:
        cfg = YouTubeRuntimeConfig(user_agent="Mozilla/5.0")
        cmd = _build_base_cmd(cfg)
        assert "--user-agent" in cmd
        assert "Mozilla/5.0" in cmd

    def test_base_cmd_with_extractor_args(self) -> None:
        cfg = YouTubeRuntimeConfig(extractor_args="youtube:player_client=mweb")
        cmd = _build_base_cmd(cfg)
        assert "--extractor-args" in cmd
        assert "youtube:player_client=mweb" in cmd

    def test_resolve_cmd(self) -> None:
        cfg = YouTubeRuntimeConfig()
        cmd = _build_resolve_cmd(cfg, "mp4", "https://youtu.be/test")
        assert "--dump-json" in cmd
        assert "--no-download" in cmd
        assert "-f" in cmd
        assert "mp4" in cmd
        assert "https://youtu.be/test" in cmd

    def test_download_cmd(self) -> None:
        cfg = YouTubeRuntimeConfig()
        cmd = _build_download_cmd(cfg, "mp4", "/tmp/%(ext)s", "https://youtu.be/test")
        assert "-f" in cmd
        assert "-o" in cmd
        assert "--merge-output-format" in cmd
        assert "mp4" in cmd

    def test_custom_executable(self) -> None:
        cfg = YouTubeRuntimeConfig(executable="/opt/maker8/bin/yt-dlp/current")
        cmd = _build_base_cmd(cfg)
        assert cmd[0] == "/opt/maker8/bin/yt-dlp/current"


# ── YtdlpError ───────────────────────────────────────────────────────────────


class TestYtdlpError:
    """Verify YtdlpError carries structured fields."""

    def test_attributes(self) -> None:
        exc = YtdlpError(
            code="YTDLP_AUTH_REQUIRED",
            message="Sign in required",
            retryable=False,
            asset_id="yt_abc",
            stderr_summary="ERROR: Sign in to confirm",
        )
        assert exc.code == "YTDLP_AUTH_REQUIRED"
        assert exc.retryable is False
        assert exc.asset_id == "yt_abc"
        assert exc.stderr_summary == "ERROR: Sign in to confirm"
        assert str(exc) == "Sign in required"

    def test_defaults(self) -> None:
        exc = YtdlpError(code="YTDLP_TIMEOUT", message="timeout", retryable=True)
        assert exc.asset_id == ""
        assert exc.stderr_summary == ""


# ── Resolve stage YtdlpError integration ─────────────────────────────────────


class TestResolveYtdlpErrors:
    """Verify ResolveAssetsStage propagates YtdlpError correctly."""

    def _make_ctx(self, assets: list[dict]) -> MagicMock:
        ctx = MagicMock()
        ctx.job_id = "test-job"
        ctx.resolved_plans = {}
        from render_contracts.render_spec import Asset

        ctx.render_spec.assets = [Asset.model_validate(a) for a in assets]
        return ctx

    def test_non_retryable_ytdlp_error(self) -> None:
        """YtdlpError with retryable=False → StageError(retryable=False)."""
        registry = MagicMock()
        connector = MagicMock()
        connector.resolve.side_effect = YtdlpError(
            code="YTDLP_VIDEO_UNAVAILABLE",
            message="Video unavailable",
            retryable=False,
            asset_id="yt_gone",
        )
        registry.get_source.return_value = connector

        stage = ResolveAssetsStage(registry)
        ctx = self._make_ctx(
            [
                {
                    "id": "yt_gone",
                    "type": "video",
                    "source": {"kind": "youtube", "url": "https://youtu.be/x", "options": {}},
                }
            ]
        )

        with pytest.raises(StageError) as exc_info:
            stage.execute(ctx)
        assert exc_info.value.code == "YTDLP_VIDEO_UNAVAILABLE"
        assert exc_info.value.retryable is False

    def test_retryable_ytdlp_error(self) -> None:
        """YtdlpError with retryable=True → StageError(retryable=True)."""
        registry = MagicMock()
        connector = MagicMock()
        connector.resolve.side_effect = YtdlpError(
            code="YTDLP_RATE_LIMITED",
            message="429 Too Many Requests",
            retryable=True,
            asset_id="yt_limited",
        )
        registry.get_source.return_value = connector

        stage = ResolveAssetsStage(registry)
        ctx = self._make_ctx(
            [
                {
                    "id": "yt_limited",
                    "type": "video",
                    "source": {"kind": "youtube", "url": "https://youtu.be/x", "options": {}},
                }
            ]
        )

        with pytest.raises(StageError) as exc_info:
            stage.execute(ctx)
        assert exc_info.value.code == "YTDLP_RATE_LIMITED"
        assert exc_info.value.retryable is True


# ── Startup helpers ──────────────────────────────────────────────────────────


class TestResolveYtdlpPath:
    """Verify resolve_ytdlp_path priority logic."""

    def test_explicit_path_wins(self, tmp_path: Path) -> None:
        result = resolve_ytdlp_path("/custom/yt-dlp", tmp_path)
        assert result == "/custom/yt-dlp"

    def test_managed_binary(self, tmp_path: Path) -> None:
        current = tmp_path / "current"
        current.write_text("#!/bin/sh\n")
        result = resolve_ytdlp_path("", tmp_path)
        assert result == str(current)

    def test_fallback_to_path(self, tmp_path: Path) -> None:
        result = resolve_ytdlp_path("", tmp_path)
        assert result == "yt-dlp"


class TestProbeYtdlp:
    """Verify probe_ytdlp returns version or None."""

    @patch("maker8.plugins.sources.youtube.subprocess.run")
    def test_returns_version(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="2025.01.15\n")
        assert probe_ytdlp("yt-dlp") == "2025.01.15"

    @patch("maker8.plugins.sources.youtube.subprocess.run")
    def test_returns_none_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError()
        assert probe_ytdlp("nonexistent") is None

    @patch("maker8.plugins.sources.youtube.subprocess.run")
    def test_returns_none_on_nonzero(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert probe_ytdlp("yt-dlp") is None
