"""RESOLVE_ASSETS stage – use source connectors to build download plans."""

from __future__ import annotations

from maker8.models.common import RenderStage
from maker8.observability.helpers import sanitize_url
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.plugins.registry import PluginRegistry
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
                    self.name, "UNSUPPORTED_SOURCE",
                    f"No connector for source kind={kind!r}",
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
                    self.name, "RESOLVE_FAILED",
                    f"Failed to resolve asset {asset.id}: {exc}",
                ) from exc
