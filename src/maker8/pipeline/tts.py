"""TTS stage – generate narration audio for every scene.

Credential rotation
~~~~~~~~~~~~~~~~~~~
At the start of each video (``execute()`` call) the stage acquires the
*next* credential from the round-robin key rings exposed by
``TTSService``.  All scenes of the same video share a single credential
so that quota usage is predictable and error-reporting is meaningful.
"""

from __future__ import annotations

from pathlib import Path

from maker8.models.common import RenderStage
from maker8.observability.helpers import Timer
from maker8.observability.metrics import DEPENDENCY_FAILURES, TTS_DURATION
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

        # ── Acquire credentials for this video (round-robin) ─────────
        google_creds: Path | None = self._tts.next_google_credentials()
        elevenlabs_key: str = self._tts.next_elevenlabs_key()

        for scene in ctx.render_spec.scenes:
            sid = scene.scene_id
            if sid in ctx.tts_results:
                continue  # already synthesised (retry-safe)

            text = scene.narration.text
            lang = scene.narration.lang or defaults.lang
            preset = scene.narration.tts_preset_ref or defaults.tts_preset_ref
            out_path = ctx.tts_dir / f"{sid}.mp3"

            log.info(
                "tts.scene.start",
                job_id=ctx.job_id,
                scene_id=sid,
                lang=lang,
                preset=preset,
                chars=len(text),
            )

            timer = Timer().start()
            try:
                result = self._tts.synthesize(
                    text,
                    lang,
                    preset,
                    out_path,
                    google_credentials_path=google_creds,
                    elevenlabs_api_key=elevenlabs_key,
                )
                timer.stop()
                ctx.tts_results[sid] = TTSResult(
                    scene_id=sid,
                    audio_path=result.audio_path,
                    duration_sec=result.duration_sec,
                )
                TTS_DURATION.labels(provider=preset).observe(timer.elapsed_sec)
                log.info(
                    "tts.scene.success",
                    job_id=ctx.job_id,
                    scene_id=sid,
                    duration_audio=result.duration_sec,
                    synthesis_sec=timer.elapsed_sec,
                )
            except TimeoutError as exc:
                timer.stop()
                DEPENDENCY_FAILURES.labels(dependency="tts").inc()
                log.error(
                    "tts.scene.failure",
                    job_id=ctx.job_id,
                    scene_id=sid,
                    error_type="TimeoutError",
                    error_message=str(exc),
                    synthesis_sec=timer.elapsed_sec,
                )
                raise StageError(
                    self.name, "TTS_TIMEOUT",
                    f"TTS synthesis timed out for scene {sid}: {exc}",
                ) from exc
            except Exception as exc:
                timer.stop()
                DEPENDENCY_FAILURES.labels(dependency="tts").inc()
                log.error(
                    "tts.scene.failure",
                    job_id=ctx.job_id,
                    scene_id=sid,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    synthesis_sec=timer.elapsed_sec,
                )
                raise StageError(
                    self.name, "TTS_FAILED",
                    f"TTS synthesis failed for scene {sid}: {exc}",
                ) from exc
