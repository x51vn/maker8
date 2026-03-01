"""VALIDATE stage – parse, validate, and canonicalize the RenderSpec."""

from __future__ import annotations

from maker8.canon import compute_job_key
from maker8.models.common import RenderStage
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_SUPPORTED_SPEC_VERSIONS = frozenset({"1.0"})


class ValidateStage(Stage):
    """Spec §3.2 mandatory rules + job-key computation."""

    @property
    def name(self) -> RenderStage:
        return RenderStage.VALIDATE

    def execute(self, ctx: PipelineContext) -> None:
        spec = ctx.render_spec

        # ── Rule: spec_version must be supported ─────────────────────
        if spec.spec_version not in _SUPPORTED_SPEC_VERSIONS:
            raise StageError(
                self.name, "UNSUPPORTED_SPEC_VERSION",
                f"spec_version={spec.spec_version!r} not in {_SUPPORTED_SPEC_VERSIONS}",
                retryable=False,
            )

        # ── Rule: canvas dimensions must be positive ─────────────────
        if spec.canvas.w <= 0 or spec.canvas.h <= 0:
            raise StageError(
                self.name, "INVALID_CANVAS",
                f"Canvas dimensions must be positive: w={spec.canvas.w}, h={spec.canvas.h}",
                retryable=False,
            )

        if spec.canvas.fps <= 0:
            raise StageError(
                self.name, "INVALID_CANVAS",
                f"Canvas fps must be positive: fps={spec.canvas.fps}",
                retryable=False,
            )

        # ── Rule: at least one scene ─────────────────────────────────
        if not spec.scenes:
            raise StageError(
                self.name, "NO_SCENES",
                "RenderSpec must contain at least one scene",
                retryable=False,
            )

        # ── Rule: all scene_ids must be unique ───────────────────────
        scene_ids = [s.scene_id for s in spec.scenes]
        if len(set(scene_ids)) != len(scene_ids):
            seen: set[str] = set()
            dupes = [sid for sid in scene_ids if sid in seen or seen.add(sid)]  # type: ignore[func-returns-value]
            raise StageError(
                self.name, "DUPLICATE_SCENE_ID",
                f"Duplicate scene_id(s): {dupes}",
                retryable=False,
            )

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

        # ── Warn: effect plugin_ids should exist ─────────────────────
        for scene in spec.scenes:
            for effect in scene.effects:
                if not effect.plugin_id:
                    log.warning(
                        "validate.empty_plugin_id",
                        scene_id=scene.scene_id,
                    )

        # ── Canonicalize + job key ───────────────────────────────────
        ctx.job_key = compute_job_key(spec)
        log.info("validate.ok", job_key=ctx.job_key, scenes=len(spec.scenes))
