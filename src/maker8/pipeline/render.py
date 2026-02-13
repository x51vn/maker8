"""RENDER stage – compose scenes into the final video.

This stage bridges ``PipelineContext`` to the rendering engine's
``RenderInput`` so that ``rendering/`` has no dependency on ``pipeline/``.
"""

from __future__ import annotations

from maker8.models.common import RenderStage
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.rendering.composer import RenderInput, compose_video
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class RenderStageImpl(Stage):
    @property
    def name(self) -> RenderStage:
        return RenderStage.RENDER

    def execute(self, ctx: PipelineContext) -> None:
        ctx.ensure_dirs()

        # Build asset_paths: prefer normalised ➜ downloaded
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

        ri = RenderInput(
            spec=ctx.render_spec,
            asset_paths=asset_paths,
            tts_audio=tts_audio,
            output_dir=ctx.output_dir,
            job_id=ctx.job_id,
        )

        try:
            video_path, meta = compose_video(ri)
            ctx.rendered_video = video_path
            ctx.output_meta = meta
            log.info(
                "render.ok",
                path=str(video_path),
                duration=meta.duration,
                size=meta.size_bytes,
            )
        except Exception as exc:
            raise StageError(
                self.name, "RENDER_FAILED",
                f"Video composition failed: {exc}",
                retryable=False,
            ) from exc
