"""DOWNLOAD stage – execute resolved plans via source connectors."""

from __future__ import annotations

from maker8.models.common import RenderStage
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.plugins.registry import PluginRegistry
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class DownloadStage(Stage):
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> RenderStage:
        return RenderStage.DOWNLOAD

    def execute(self, ctx: PipelineContext) -> None:
        ctx.ensure_dirs()

        for asset_id, plan in ctx.resolved_plans.items():
            if asset_id in ctx.downloaded_assets:
                continue  # already downloaded (retry-safe)

            connector = self._registry.get_source(plan.source_kind)
            try:
                local_path = connector.download(plan, ctx.assets_dir)
                ctx.downloaded_assets[asset_id] = local_path
                # Record asset in report for RenderResult
                ctx.asset_report.append({
                    "asset_id": asset_id,
                    "source_kind": plan.source_kind,
                    "filename": local_path.name,
                    "size_bytes": local_path.stat().st_size if local_path.exists() else 0,
                })
                log.info("download.ok", asset_id=asset_id, path=str(local_path))
            except Exception as exc:
                raise StageError(
                    self.name, "DOWNLOAD_FAILED",
                    f"Failed to download asset {asset_id}: {exc}",
                ) from exc
