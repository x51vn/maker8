"""RESOLVE_ASSETS stage – use source connectors to build download plans.

When an asset fails with a **non-retryable** error the stage records a
warning, tries any ``fallback_asset_refs`` declared on the referring
layer, and continues.  The downstream RENDER stage already handles
missing assets via ``missing_asset_policy``.

A **retryable** error (rate-limit, network, server 5xx) still bubbles up
so the orchestrator can retry the whole stage.
"""

from __future__ import annotations

from maker8.models.common import AssetWarning, RenderStage
from maker8.observability.helpers import sanitize_url
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.plugins.registry import PluginRegistry
from maker8.plugins.sources.youtube import YtdlpError
from maker8.retry import StageError
from maker8.utils.logging import get_logger
from render_contracts.render_spec import Asset

log = get_logger(__name__)


class ResolveAssetsStage(Stage):
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> RenderStage:
        return RenderStage.RESOLVE_ASSETS

    def execute(self, ctx: PipelineContext) -> None:
        # Index assets by id for fast fallback lookup.
        asset_index: dict[str, Asset] = {a.id: a for a in ctx.render_spec.assets}

        # Build fallback chains: primary_asset_id → [fallback_asset_ids…]
        fallback_map: dict[str, list[str]] = {}
        for scene in ctx.render_spec.scenes:
            for layer in scene.layers:
                if layer.asset_ref and layer.fallback_asset_refs:
                    fallback_map[layer.asset_ref] = list(layer.fallback_asset_refs)

        for asset in ctx.render_spec.assets:
            if asset.id in ctx.resolved_plans:
                continue  # already resolved (e.g. via fallback promotion)
            self._resolve_one(ctx, asset, asset_index, fallback_map)

    # ── Per-asset resolution with fallback chain ─────────────────────

    def _resolve_one(
        self,
        ctx: PipelineContext,
        asset: Asset,
        asset_index: dict[str, Asset],
        fallback_map: dict[str, list[str]],
    ) -> None:
        """Resolve *asset*; on non-retryable failure try its fallback chain."""
        kind = asset.source.kind
        asset_type = asset.type
        source_url = sanitize_url(getattr(asset.source, "url", "") or "")

        try:
            connector = self._registry.get_source(kind)
        except KeyError:
            self._record_failure(
                ctx,
                asset.id,
                "UNSUPPORTED_SOURCE",
                f"No connector for source kind={kind!r}",
                retryable=False,
            )
            self._try_fallbacks(ctx, asset.id, asset_index, fallback_map)
            return

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
            return  # success
        except ValueError as exc:
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
            self._record_failure(
                ctx,
                asset.id,
                code,
                f"Failed to resolve asset {asset.id}: {exc}",
                retryable=False,
            )
        except YtdlpError as exc:
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
            if exc.retryable:
                # Transient error — let orchestrator retry the whole stage.
                raise StageError(
                    self.name,
                    exc.code,
                    str(exc),
                    retryable=True,
                ) from exc
            self._record_failure(
                ctx,
                asset.id,
                exc.code,
                str(exc),
                retryable=False,
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
            # Unknown errors are assumed retryable to avoid data loss.
            raise StageError(
                self.name,
                "RESOLVE_FAILED",
                f"Failed to resolve asset {asset.id}: {exc}",
            ) from exc

        # Non-retryable failure — try fallback assets.
        self._try_fallbacks(ctx, asset.id, asset_index, fallback_map)

    # ── Fallback helpers ─────────────────────────────────────────────

    def _try_fallbacks(
        self,
        ctx: PipelineContext,
        failed_asset_id: str,
        asset_index: dict[str, Asset],
        fallback_map: dict[str, list[str]],
    ) -> None:
        """Try fallback assets for *failed_asset_id*.

        On success the resolved plan is stored under the **original**
        (primary) asset_id so that downstream stages find it where they
        expect it — no layer rewrite needed.
        """
        fallbacks = fallback_map.get(failed_asset_id, [])
        for fb_id in fallbacks:
            fb_asset = asset_index.get(fb_id)
            if fb_asset is None:
                continue
            log.info(
                "resolve.asset.fallback_attempt",
                job_id=ctx.job_id,
                primary_asset_id=failed_asset_id,
                fallback_asset_id=fb_id,
            )
            try:
                connector = self._registry.get_source(fb_asset.source.kind)
                source_dict = fb_asset.source.model_dump(mode="json", by_alias=True)
                plan = connector.resolve(fb_id, source_dict)
                # Store under the PRIMARY id so the layer's asset_ref still resolves.
                ctx.resolved_plans[failed_asset_id] = plan
                log.info(
                    "resolve.asset.fallback_success",
                    job_id=ctx.job_id,
                    primary_asset_id=failed_asset_id,
                    fallback_asset_id=fb_id,
                    filename=plan.filename,
                )
                # Update the warning to note which fallback was used.
                for w in ctx.warnings:
                    if w.asset_id == failed_asset_id and w.stage == "RESOLVE_ASSETS":
                        w.fallback_used = fb_id
                return  # fallback succeeded
            except Exception as fb_exc:
                log.warning(
                    "resolve.asset.fallback_failure",
                    job_id=ctx.job_id,
                    primary_asset_id=failed_asset_id,
                    fallback_asset_id=fb_id,
                    error_type=type(fb_exc).__name__,
                    error_message=str(fb_exc),
                )
                continue

        # All fallbacks exhausted — asset stays in failed_assets.
        # Downstream RENDER stage will apply missing_asset_policy.
        if fallbacks:
            log.warning(
                "resolve.asset.all_fallbacks_exhausted",
                job_id=ctx.job_id,
                asset_id=failed_asset_id,
                fallback_count=len(fallbacks),
            )

    def _record_failure(
        self,
        ctx: PipelineContext,
        asset_id: str,
        code: str,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        """Record an asset failure without crashing the stage."""
        ctx.failed_assets.add(asset_id)
        ctx.warnings.append(
            AssetWarning(
                asset_id=asset_id,
                stage="RESOLVE_ASSETS",
                code=code,
                message=message,
            )
        )
        log.warning(
            "resolve.asset.skipped",
            job_id=ctx.job_id,
            asset_id=asset_id,
            error_code=code,
            retryable=retryable,
        )


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
