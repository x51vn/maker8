"""RENDER stage – compose scenes into the final video.

This stage bridges ``PipelineContext`` to the rendering engine's
``RenderInput`` so that ``rendering/`` has no dependency on ``pipeline/``.
"""

from __future__ import annotations

from maker8.models.common import RenderStage
from maker8.observability.helpers import Timer
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.plugins.registry import PluginRegistry
from maker8.rendering.composer import RenderInput, _RenderTimeout, compose_video
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class RenderStageImpl(Stage):
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

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

        # Build TTS map
        tts_audio = {
            sid: (r.audio_path, r.duration_sec)
            for sid, r in ctx.tts_results.items()
        }

        # Resolve effect plugins referenced by scenes
        effects_map = {}
        for scene in ctx.render_spec.scenes:
            for ei in scene.effects:
                if ei.plugin_id not in effects_map:
                    try:
                        effects_map[ei.plugin_id] = self._registry.get_effect(
                            ei.plugin_id
                        )
                    except KeyError:
                        log.warning("render.effect_not_found", plugin_id=ei.plugin_id)

        ri = RenderInput(
            spec=ctx.render_spec,
            asset_paths=asset_paths,  # type: ignore[arg-type]
            tts_audio=tts_audio,
            output_dir=ctx.output_dir,
            job_id=ctx.job_id,
            effects_map=effects_map,
        )

        log.info(
            "render.start",
            job_id=ctx.job_id,
            scenes=len(ctx.render_spec.scenes),
            assets=len(asset_paths),
            tts_scenes=len(tts_audio),
        )

        timer = Timer().start()
        try:
            video_path, meta = compose_video(ri)
            timer.stop()
            ctx.rendered_video = video_path
            ctx.output_meta = meta
            log.info(
                "render.success",
                job_id=ctx.job_id,
                path=str(video_path),
                duration=meta.duration,
                size=meta.size_bytes,
                render_sec=timer.elapsed_sec,
            )
        except _RenderTimeout as exc:
            timer.stop()
            log.error(
                "render.timeout",
                job_id=ctx.job_id,
                render_sec=timer.elapsed_sec,
            )
            raise StageError(
                self.name, "RENDER_TIMEOUT",
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
                self.name, "RENDER_FAILED",
                f"Video composition failed: {exc}",
                retryable=False,
            ) from exc
