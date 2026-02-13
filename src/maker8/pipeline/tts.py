"""TTS stage – generate narration audio for every scene."""

from __future__ import annotations

from maker8.models.common import RenderStage
from maker8.pipeline.context import PipelineContext, TTSResult
from maker8.pipeline.stage import Stage
from maker8.retry import StageError
from maker8.services.tts_client import TTSService
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class TTSStage(Stage):
    def __init__(self, tts_service: TTSService) -> None:
        self._tts = tts_service

    @property
    def name(self) -> RenderStage:
        return RenderStage.TTS

    def execute(self, ctx: PipelineContext) -> None:
        ctx.ensure_dirs()
        defaults = ctx.render_spec.defaults.narration

        for scene in ctx.render_spec.scenes:
            sid = scene.scene_id
            if sid in ctx.tts_results:
                continue  # already synthesised (retry-safe)

            text = scene.narration.text
            lang = scene.narration.lang or defaults.lang
            preset = scene.narration.tts_preset_ref or defaults.tts_preset_ref
            out_path = ctx.tts_dir / f"{sid}.mp3"

            try:
                result = self._tts.synthesize(text, lang, preset, out_path)
                ctx.tts_results[sid] = TTSResult(
                    scene_id=sid,
                    audio_path=result.audio_path,
                    duration_sec=result.duration_sec,
                )
                log.info("tts.ok", scene_id=sid, duration=result.duration_sec)
            except Exception as exc:
                raise StageError(
                    self.name, "TTS_FAILED",
                    f"TTS synthesis failed for scene {sid}: {exc}",
                ) from exc
