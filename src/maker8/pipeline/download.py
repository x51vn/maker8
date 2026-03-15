"""DOWNLOAD stage – execute resolved plans via source connectors."""

from __future__ import annotations

from maker8.models.common import AssetWarning, RenderStage
from maker8.observability.metrics import DOWNLOAD_BYTES
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.plugins.registry import PluginRegistry
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
            if asset_id in ctx.failed_assets:
                continue  # already failed in a previous stage

            connector = self._registry.get_source(plan.source_kind)
            log.info(
                "download.asset.start",
                job_id=ctx.job_id,
                asset_id=asset_id,
                source_kind=plan.source_kind,
            )

            try:
                local_path = connector.download(plan, ctx.assets_dir)
                ctx.downloaded_assets[asset_id] = local_path
                size_bytes = local_path.stat().st_size if local_path.exists() else 0
                # Record asset in report for RenderResult
                ctx.asset_report.append({
                    "asset_id": asset_id,
                    "source_kind": plan.source_kind,
                    "filename": local_path.name,
                    "size_bytes": size_bytes,
                })
                DOWNLOAD_BYTES.labels(source_kind=plan.source_kind).observe(size_bytes)
                log.info(
                    "download.asset.success",
                    job_id=ctx.job_id,
                    asset_id=asset_id,
                    source_kind=plan.source_kind,
                    path=str(local_path),
                    size_bytes=size_bytes,
                )
            except Exception as exc:
                # Isolate per-asset failure: mark as failed and continue.
                ctx.failed_assets.add(asset_id)
                ctx.warnings.append(AssetWarning(
                    asset_id=asset_id,
                    stage="DOWNLOAD",
                    code="DOWNLOAD_FAILED",
                    message=f"Failed to download asset {asset_id}: {exc}",
                    fallback_used="asset_skipped",
                ))
                log.warning(
                    "download.asset.skipped",
                    job_id=ctx.job_id,
                    asset_id=asset_id,
                    source_kind=plan.source_kind,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
