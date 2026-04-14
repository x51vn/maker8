"""VALIDATE stage – parse, validate, and canonicalize the RenderSpec."""

from __future__ import annotations

from maker8.canon import compute_job_key
from maker8.models.common import RenderStage
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.retry import StageError
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_SUPPORTED_SPEC_VERSIONS = frozenset({"1.0", "2.0"})

_ALLOWED_ROLES = frozenset(
    {
        "primary_visual",
        "supporting_visual",
        "title",
        "logo",
        "cta",
        "decorative_text",
    }
)

_ALLOWED_MISSING_ASSET_POLICIES = frozenset(
    {
        "drop_layer",
        "skip_scene",
        "scene_placeholder",
        "fail_request",
    }
)

_ALLOWED_SUBTITLE_SOURCES = frozenset({"narration", "custom"})


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
                self.name,
                "UNSUPPORTED_SPEC_VERSION",
                f"spec_version={spec.spec_version!r} not in {_SUPPORTED_SPEC_VERSIONS}",
                retryable=False,
            )

        # ── Rule: envelope and render_spec spec_version must match ───
        req_version = ctx.request.spec_version if ctx.request is not None else spec.spec_version
        if req_version != spec.spec_version:
            raise StageError(
                self.name,
                "SPEC_VERSION_MISMATCH",
                (
                    f"request.spec_version={req_version!r} does not match "
                    f"render_spec.spec_version={spec.spec_version!r}; "
                    "both must be identical"
                ),
                retryable=False,
            )

        # ── Rule: canvas dimensions must be positive ─────────────────
        if spec.canvas.w <= 0 or spec.canvas.h <= 0:
            raise StageError(
                self.name,
                "INVALID_CANVAS",
                f"Canvas dimensions must be positive: w={spec.canvas.w}, h={spec.canvas.h}",
                retryable=False,
            )

        if spec.canvas.fps <= 0:
            raise StageError(
                self.name,
                "INVALID_CANVAS",
                f"Canvas fps must be positive: fps={spec.canvas.fps}",
                retryable=False,
            )

        # ── Rule: at least one scene ─────────────────────────────────
        if not spec.scenes:
            raise StageError(
                self.name,
                "NO_SCENES",
                "RenderSpec must contain at least one scene",
                retryable=False,
            )

        # ── Rule: all scene_ids must be unique ───────────────────────
        scene_ids = [s.scene_id for s in spec.scenes]
        if len(set(scene_ids)) != len(scene_ids):
            seen: set[str] = set()
            dupes = [sid for sid in scene_ids if sid in seen or seen.add(sid)]  # type: ignore[func-returns-value]
            raise StageError(
                self.name,
                "DUPLICATE_SCENE_ID",
                f"Duplicate scene_id(s): {dupes}",
                retryable=False,
            )

        # ── Rule: all assets must have unique IDs ────────────────────
        asset_ids = {a.id for a in spec.assets}
        if len(asset_ids) != len(spec.assets):
            raise StageError(
                self.name,
                "DUPLICATE_ASSET_ID",
                "Duplicate asset IDs found in assets[]",
                retryable=False,
            )

        # ── Rule: scenes must have narration.text ────────────────────
        for scene in spec.scenes:
            if not (scene.narration.text and scene.narration.text.strip()):
                raise StageError(
                    self.name,
                    "EMPTY_NARRATION",
                    f"Scene {scene.scene_id} has empty narration.text",
                    retryable=False,
                )

        # ── Rule: all asset_refs must resolve ────────────────────────
        for scene in spec.scenes:
            for layer in scene.layers:
                if layer.asset_ref and layer.asset_ref not in asset_ids:
                    raise StageError(
                        self.name,
                        "UNKNOWN_ASSET_REF",
                        f"Layer {layer.layer_id} references unknown asset {layer.asset_ref!r}",
                        retryable=False,
                    )
            for track in scene.audio_tracks:
                if track.asset_ref not in asset_ids:
                    raise StageError(
                        self.name,
                        "UNKNOWN_ASSET_REF",
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

        # ── V2-specific validations ──────────────────────────────────
        if spec.spec_version == "2.0":
            self._validate_v2(ctx)

        # ── Canonicalize + job key ───────────────────────────────────
        ctx.job_key = compute_job_key(spec)
        log.info(
            "validate.success",
            job_id=ctx.job_id,
            job_key=ctx.job_key,
            spec_version=spec.spec_version,
            scenes=len(spec.scenes),
            assets=len(spec.assets),
            canvas=f"{spec.canvas.w}x{spec.canvas.h}@{spec.canvas.fps}",
        )

        # ── Warn: identity consistency (non-blocking) ────────────────
        self._warn_identity(ctx)

    def _validate_v2(self, ctx: PipelineContext) -> None:
        """Extra validations for spec_version 2.0."""
        spec = ctx.render_spec
        req = ctx.request

        # ── planning.planned_scene_count must match actual scenes ────
        if req.planning is not None and req.planning.planned_scene_count is not None:
            actual = len(spec.scenes)
            if req.planning.planned_scene_count != actual:
                raise StageError(
                    self.name,
                    "PLANNING_SCENE_COUNT_MISMATCH",
                    f"planning.planned_scene_count={req.planning.planned_scene_count}"
                    f" but render_spec has {actual} scene(s)",
                    retryable=False,
                )

        # ── Layer role and missing_asset_policy ──────────────────────
        for scene in spec.scenes:
            for layer in scene.layers:
                if layer.role is not None and layer.role not in _ALLOWED_ROLES:
                    raise StageError(
                        self.name,
                        "INVALID_LAYER_ROLE",
                        f"Layer {layer.layer_id} has unknown role={layer.role!r};"
                        f" allowed: {sorted(_ALLOWED_ROLES)}",
                        retryable=False,
                    )
                if layer.missing_asset_policy not in _ALLOWED_MISSING_ASSET_POLICIES:
                    raise StageError(
                        self.name,
                        "INVALID_MISSING_ASSET_POLICY",
                        f"Layer {layer.layer_id} has unknown"
                        f" missing_asset_policy={layer.missing_asset_policy!r};"
                        f" allowed: {sorted(_ALLOWED_MISSING_ASSET_POLICIES)}",
                        retryable=False,
                    )

        # ── Each V2 scene must have a primary_visual layer ──────────
        # Compatibility: infer from a single obvious visual layer when
        # upstream omitted role during migration.
        for scene in spec.scenes:
            has_primary_visual = any(layer.role == "primary_visual" for layer in scene.layers)
            if has_primary_visual:
                continue

            inferred_layer = self._infer_primary_visual(scene.layers)
            if inferred_layer is not None:
                prev_role = inferred_layer.role
                # Store inferred values on context instead of mutating spec.
                ctx.inferred_roles[inferred_layer.layer_id] = "primary_visual"
                ctx.inferred_required.add(inferred_layer.layer_id)
                log.warning(
                    "validate.primary_visual_inferred",
                    job_id=ctx.job_id,
                    scene_id=scene.scene_id,
                    layer_id=inferred_layer.layer_id,
                    previous_role=prev_role,
                )
                continue

            raise StageError(
                self.name,
                "MISSING_PRIMARY_VISUAL",
                f"Scene {scene.scene_id} has no layer with role='primary_visual';"
                " V2 requires each production scene to have a primary visual layer;"
                " no inferable image/video layer was found",
                retryable=False,
            )

        # ── Scene subtitle validation ────────────────────────────────
        for scene in spec.scenes:
            sub = scene.subtitle
            if sub is None:
                continue
            if sub.source not in _ALLOWED_SUBTITLE_SOURCES:
                raise StageError(
                    self.name,
                    "INVALID_SUBTITLE_SOURCE",
                    f"Scene {scene.scene_id} has subtitle.source={sub.source!r};"
                    f" allowed: {sorted(_ALLOWED_SUBTITLE_SOURCES)}",
                    retryable=False,
                )
            if sub.source == "custom" and not (sub.text and sub.text.strip()):
                raise StageError(
                    self.name,
                    "MISSING_SUBTITLE_TEXT",
                    f"Scene {scene.scene_id} has subtitle.source='custom' but text is empty",
                    retryable=False,
                )

    def _warn_identity(self, ctx: PipelineContext) -> None:
        """Non-blocking identity consistency checks (D-3: warn, never patch)."""
        req = ctx.request
        if req is None:
            return

        channel_id = req.uploader_metadata.channel_id if req.uploader_metadata else ""
        publish = ctx.render_spec.publish
        enabled_targets = [t for t in publish.targets if t.enabled] if publish else []

        if not enabled_targets:
            return

        first_account_ref = next((t.account_ref for t in enabled_targets if t.account_ref), "")

        if not channel_id:
            log.warning(
                "validate.identity_empty_channel_id",
                job_id=ctx.job_id,
                enabled_targets=len(enabled_targets),
                hint="uploader_metadata.channel_id is empty but enabled publish targets exist",
            )
        elif first_account_ref and channel_id != first_account_ref:
            log.warning(
                "validate.identity_mismatch",
                job_id=ctx.job_id,
                channel_id=channel_id,
                account_ref=first_account_ref,
                hint="channel_id does not match first enabled target's account_ref",
            )

    @staticmethod
    def _infer_primary_visual(layers: list[object]) -> object | None:
        """Pick a single obvious visual layer to promote as primary_visual."""
        candidates: list[object] = []
        for layer in layers:
            layer_type = getattr(layer, "type", "")
            layer_role = getattr(layer, "role", None)
            has_primary_asset_ref = bool(getattr(layer, "asset_ref", None))
            has_fallback_refs = bool(getattr(layer, "fallback_asset_refs", []))
            is_eligible_role = layer_role in (None, "supporting_visual")
            if (
                layer_type in {"image", "video"}
                and (has_primary_asset_ref or has_fallback_refs)
                and is_eligible_role
            ):
                candidates.append(layer)

        if len(candidates) != 1:
            return None
        return candidates[0]
