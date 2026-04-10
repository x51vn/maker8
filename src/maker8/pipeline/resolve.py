"""RESOLVE_ASSETS stage – use source connectors to build download plans."""

from __future__ import annotations

from maker8.models.common import RenderStage
from maker8.observability.helpers import sanitize_url
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.plugins.registry import PluginRegistry
from maker8.plugins.sources.youtube import YtdlpError
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class ResolveAssetsStage(Stage):
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> RenderStage:
        return RenderStage.RESOLVE_ASSETS

    def execute(self, ctx: PipelineContext) -> None:
        for asset in ctx.render_spec.assets:
            kind = asset.source.kind
            asset_type = asset.type
            source_url = sanitize_url(getattr(asset.source, "url", "") or "")

            try:
                connector = self._registry.get_source(kind)
            except KeyError as exc:
                raise StageError(
                    self.name,
                    "UNSUPPORTED_SOURCE",
                    f"No connector for source kind={kind!r}",
                    retryable=False,
                ) from exc

            connector_name = type(connector).__name__
            source_dict = asset.source.model_dump(mode="json", by_alias=True)

            log.info(
                "resolve.asset.start",
                job_id=ctx.job_id,
                asset_id=asset.id,
                asset_type=asset_type,
                source_kind=kind,
                connector=connector_name,
                url=source_url,
            )

            try:
                plan = connector.resolve(asset.id, source_dict)
                ctx.resolved_plans[asset.id] = plan
                log.info(
                    "resolve.asset.success",
                    job_id=ctx.job_id,
                    asset_id=asset.id,
                    source_kind=kind,
                    filename=plan.filename,
                    expected_type=plan.expected_type,
                )
            except ValueError as exc:
                # Deterministic input/config errors — do not retry
                log.error(
                    "resolve.asset.failure",
                    job_id=ctx.job_id,
                    asset_id=asset.id,
                    asset_type=asset_type,
                    source_kind=kind,
                    connector=connector_name,
                    url=source_url,
                    format_spec=asset.source.options.format,
                    max_duration_sec=asset.source.options.max_duration_sec,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    retryable=False,
                )
                code = _classify_value_error(str(exc))
                raise StageError(
                    self.name,
                    code,
                    f"Failed to resolve asset {asset.id}: {exc}",
                    retryable=False,
                ) from exc
            except YtdlpError as exc:
                # Structured yt-dlp error with classification already done
                log.error(
                    "resolve.asset.failure",
                    job_id=ctx.job_id,
                    asset_id=asset.id,
                    asset_type=asset_type,
                    source_kind=kind,
                    connector=connector_name,
                    url=source_url,
                    error_type="YtdlpError",
                    error_code=exc.code,
                    error_message=str(exc),
                    stderr_summary=exc.stderr_summary,
                    retryable=exc.retryable,
                )
                raise StageError(
                    self.name,
                    exc.code,
                    str(exc),
                    retryable=exc.retryable,
                ) from exc
            except Exception as exc:
                log.error(
                    "resolve.asset.failure",
                    job_id=ctx.job_id,
                    asset_id=asset.id,
                    asset_type=asset_type,
                    source_kind=kind,
                    connector=connector_name,
                    url=source_url,
                    format_spec=asset.source.options.format,
                    max_duration_sec=asset.source.options.max_duration_sec,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                raise StageError(
                    self.name,
                    "RESOLVE_FAILED",
                    f"Failed to resolve asset {asset.id}: {exc}",
                ) from exc


def _classify_value_error(message: str) -> str:
    """Map deterministic ValueError messages to specific error codes."""
    msg = message.lower()
    if "url" in msg:
        return "INVALID_SOURCE_URL"
    if "format" in msg:
        return "INVALID_YTDLP_FORMAT"
    if "duration" in msg:
        return "INVALID_SOURCE_OPTIONS"
    return "INVALID_SOURCE_CONFIG"
