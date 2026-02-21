"""VALIDATE stage – parse, validate, and canonicalize the RenderSpec."""

from __future__ import annotations

from maker8.canon import compute_job_key
from maker8.models.common import RenderStage
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class ValidateStage(Stage):
    """Spec §3.2 mandatory rules + job-key computation."""

    @property
    def name(self) -> RenderStage:
        return RenderStage.VALIDATE

    def execute(self, ctx: PipelineContext) -> None:
        spec = ctx.render_spec

        # ── Rule: all assets must have unique IDs ────────────────────
        asset_ids = {a.id for a in spec.assets}
        if len(asset_ids) != len(spec.assets):
            raise StageError(
                self.name, "DUPLICATE_ASSET_ID",
                "Duplicate asset IDs found in assets[]",
                retryable=False,
            )

        # ── Rule: scenes must have narration.text ────────────────────
        for scene in spec.scenes:
            if not (scene.narration.text and scene.narration.text.strip()):
                raise StageError(
                    self.name, "EMPTY_NARRATION",
                    f"Scene {scene.scene_id} has empty narration.text",
                    retryable=False,
                )

        # ── Rule: all asset_refs must resolve ────────────────────────
        for scene in spec.scenes:
            for layer in scene.layers:
                if layer.asset_ref and layer.asset_ref not in asset_ids:
                    raise StageError(
                        self.name, "UNKNOWN_ASSET_REF",
                        f"Layer {layer.layer_id} references unknown asset {layer.asset_ref!r}",
                        retryable=False,
                    )
            for track in scene.audio_tracks:
                if track.asset_ref not in asset_ids:
                    raise StageError(
                        self.name, "UNKNOWN_ASSET_REF",
                        f"Audio track references unknown asset {track.asset_ref!r}",
                        retryable=False,
                    )

        # ── Canonicalize + job key ───────────────────────────────────
        ctx.job_key = compute_job_key(spec)
        log.info("validate.ok", job_key=ctx.job_key, scenes=len(spec.scenes))
