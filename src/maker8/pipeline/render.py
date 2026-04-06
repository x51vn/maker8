"""RENDER stage – compose scenes into the final video.

This stage bridges ``PipelineContext`` to the rendering engine's
``RenderInput`` so that ``rendering/`` has no dependency on ``pipeline/``.
"""

from __future__ import annotations

from maker8.models.common import AssetWarning, RenderStage
from maker8.models.spec import RenderSpec
from maker8.observability.helpers import Timer
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.plugins.registry import PluginRegistry
from maker8.rendering.composer import RenderInput, _RenderTimeoutError, compose_video
from maker8.rendering.perf_profile import PerfProfile
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class RenderStageImpl(Stage):
    def __init__(
        self,
        registry: PluginRegistry,
        perf_profile: PerfProfile | None = None,
    ) -> None:
        self._registry = registry
        self._perf_profile = perf_profile

    @property
    def name(self) -> RenderStage:
        return RenderStage.RENDER

    def execute(self, ctx: PipelineContext) -> None:
        ctx.ensure_dirs()

        # Build asset_paths: prefer normalised → downloaded
        asset_paths = {
            aid: ctx.asset_path(aid)
            for aid in ctx.downloaded_assets
            if ctx.asset_path(aid) is not None
        }

        # ── Scene-level viability check ──────────────────────────────
        # A scene is viable if it has at least one layer whose asset_ref
        # is resolved (in asset_paths) or is a text layer (no asset_ref).
        viable_scenes = []
        for scene in ctx.render_spec.scenes:
            if scene.scene_id in ctx.skipped_scenes:
                continue
            has_content = any(
                layer.type == "text" or (layer.asset_ref and layer.asset_ref in asset_paths)
                for layer in scene.layers
            )
            if has_content:
                viable_scenes.append(scene)
            else:
                ctx.skipped_scenes.add(scene.scene_id)
                ctx.warnings.append(
                    AssetWarning(
                        asset_id="",
                        scene_id=scene.scene_id,
                        stage="RENDER",
                        code="SCENE_NO_CONTENT",
                        message=f"Scene {scene.scene_id} skipped: all layer assets missing",
                        fallback_used="scene_skipped",
                    )
                )
                log.warning(
                    "render.scene.skipped",
                    job_id=ctx.job_id,
                    scene_id=scene.scene_id,
                    reason="all_layer_assets_missing",
                )

        if not viable_scenes:
            raise StageError(
                self.name,
                "ALL_SCENES_SKIPPED",
                "All scenes have no renderable content after degradation",
                retryable=False,
            )

        # Build a filtered spec for the composer
        filtered_spec = RenderSpec(
            **{
                **ctx.render_spec.model_dump(mode="python"),
                "scenes": viable_scenes,
            },
        )

        # Build TTS map
        tts_audio = {sid: (r.audio_path, r.duration_sec) for sid, r in ctx.tts_results.items()}

        # Resolve effect plugins referenced by scenes
        effects_map = {}
        for scene in filtered_spec.scenes:
            for ei in scene.effects:
                if ei.plugin_id not in effects_map:
                    try:
                        effects_map[ei.plugin_id] = self._registry.get_effect(ei.plugin_id)
                    except KeyError:
                        log.warning("render.effect_not_found", plugin_id=ei.plugin_id)

        ri = RenderInput(
            spec=filtered_spec,
            asset_paths=asset_paths,  # type: ignore[arg-type]
            tts_audio=tts_audio,
            output_dir=ctx.output_dir,
            job_id=ctx.job_id,
            effects_map=effects_map,
            perf_profile=self._perf_profile,
        )

        log.info(
            "render.start",
            job_id=ctx.job_id,
            scenes=len(filtered_spec.scenes),
            scenes_skipped=len(ctx.skipped_scenes),
            assets=len(asset_paths),
            tts_scenes=len(tts_audio),
            degraded=ctx.is_degraded,
        )

        timer = Timer().start()
        try:
            video_path, meta = compose_video(ri)
            timer.stop()
            # Propagate layer-level warnings collected during composition.
            ctx.warnings.extend(ri.warnings)
            ctx.rendered_video = video_path
            ctx.output_meta = meta
            log.info(
                "render.success",
                job_id=ctx.job_id,
                path=str(video_path),
                duration=meta.duration,
                size=meta.size_bytes,
                render_sec=timer.elapsed_sec,
                degraded=ctx.is_degraded,
            )
        except _RenderTimeoutError as exc:
            timer.stop()
            log.error(
                "render.timeout",
                job_id=ctx.job_id,
                render_sec=timer.elapsed_sec,
            )
            raise StageError(
                self.name,
                "RENDER_TIMEOUT",
                f"Video composition timed out after {timer.elapsed_sec}s",
                retryable=False,
            ) from exc
        except Exception as exc:
            timer.stop()
            log.error(
                "render.failure",
                job_id=ctx.job_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
                render_sec=timer.elapsed_sec,
            )
            raise StageError(
                self.name,
                "RENDER_FAILED",
                f"Video composition failed: {exc}",
                retryable=False,
            ) from exc
