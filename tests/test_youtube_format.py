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
    YouTubeSourceConnector,
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
        ctx = self._make_ctx([{
            "id": "yt_bad",
            "type": "video",
            "source": {"kind": "youtube", "url": "", "options": {}},
        }])

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
        ctx = self._make_ctx([{
            "id": "yt_bad",
            "type": "video",
            "source": {
                "kind": "youtube",
                "url": "https://youtu.be/test",
                "options": {"format": ""},
            },
        }])

        with pytest.raises(StageError) as exc_info:
            stage.execute(ctx)
        assert exc_info.value.code == "INVALID_YTDLP_FORMAT"
        assert exc_info.value.retryable is False

    def test_unsupported_source_is_non_retryable(self) -> None:
        """Unknown source kind should be UNSUPPORTED_SOURCE, non-retryable."""
        registry = MagicMock()
        registry.get_source.side_effect = KeyError("badkind")

        stage = ResolveAssetsStage(registry)
        ctx = self._make_ctx([{
            "id": "asset_bad",
            "type": "video",
            "source": {"kind": "badkind", "url": "http://x", "options": {}},
        }])

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
        ctx = self._make_ctx([{
            "id": "yt_null_fmt",
            "type": "video",
            "source": {
                "kind": "youtube",
                "url": "https://youtu.be/test",
                "options": {"format": None},
            },
        }])

        stage.execute(ctx)  # Should not raise
        assert "yt_null_fmt" in ctx.resolved_plans
        assert ctx.resolved_plans["yt_null_fmt"].format_spec == _DEFAULT_FORMAT
