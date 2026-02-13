"""RESOLVE_ASSETS stage – use source connectors to build download plans."""

from __future__ import annotations

from maker8.models.common import RenderStage
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
            try:
                connector = self._registry.get_source(kind)
            except KeyError as exc:
                raise StageError(
                    self.name, "UNSUPPORTED_SOURCE",
                    f"No connector for source kind={kind!r}",
                ) from exc

            source_dict = asset.source.model_dump(mode="json", by_alias=True)
            try:
                plan = connector.resolve(asset.id, source_dict)
                ctx.resolved_plans[asset.id] = plan
                log.info("resolve.ok", asset_id=asset.id, kind=kind)
            except Exception as exc:
                raise StageError(
                    self.name, "RESOLVE_FAILED",
                    f"Failed to resolve asset {asset.id}: {exc}",
                ) from exc
