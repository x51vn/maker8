## Context

`SceneDetectStage` runs FFmpeg scene detection on video assets flagged with `scene_detect_enabled: true`, producing `SceneBoundary(start_sec, end_sec)` intervals stored in `ctx.scene_candidates`. These are collected per asset-id but are never passed to the rendering layer. The `Layer` model already has a `trim: Trim | None` field and `_build_video` already applies it — the composer already knows how to play a specific segment of a video clip. The gap is purely a wiring and model gap: no API exists for callers to say "pick scene N from this asset", and `scene_candidates` is dropped before `RenderInput` is constructed.

## Goals / Non-Goals

**Goals:**
- Add `scene_clip_select: str | None` to `Layer` so callers can declare a selection strategy
- Pass `ctx.scene_candidates` into `RenderInput` so the composer can resolve selection at render time
- Implement `_resolve_scene_trim(layer, scene_candidates)` inside the composer to convert strategy → `Trim`
- Extend `build_layer_clip` / `_build_video` to accept an `effective_trim` override that takes precedence over `layer.trim`
- Warn (never fail) when `scene_clip_select` is set but no candidates exist for the asset

**Non-Goals:**
- Changing how or when scene detection runs (that is `SceneDetectStage`'s scope)
- Automatic scene selection without opt-in (callers must set `scene_clip_select`)
- Selecting scenes across multiple assets — selection is per-layer, per-asset
- Storing selected boundary back in the result/manifest

## Decisions

**Decision: Resolution happens in the composer, not in `RenderStageImpl`**

Rationale: The composer already owns all clip-assembly logic. Adding selection resolution there keeps `RenderStageImpl` as a pure bridge stage (context → `RenderInput`). Placing it in `RenderStageImpl` would require mutating or copying `Layer` objects, which are Pydantic wire-format models that should not be mutated by pipeline stages.

Alternatives considered:
- Resolve in `RenderStageImpl` by building a `Trim` and attaching it to a context-side override map keyed by `(scene_id, layer_id)`. Rejected: more indirection, more state, harder to test.
- Mutate `Layer.trim` in-place before passing to composer. Rejected: wire-format models must not be mutated mid-pipeline; this breaks round-trip guarantees.

**Decision: `effective_trim` is passed as an explicit parameter to `build_layer_clip` / `_build_video`**

Rather than teaching `build_layer_clip` to look up scene candidates itself (which would require passing the full `scene_candidates` dict and the resolution logic into `layers.py`), the composer resolves the trim first and passes the result as `effective_trim: Trim | None`. `_build_video` uses `effective_trim` if not `None`, otherwise falls back to `layer.trim`. This keeps `layers.py` free from selection logic.

**Decision: Strategy strings are simple, not enums**

`scene_clip_select` is a `str | None` on the wire (not an enum) for forward compatibility. Unknown strategies are treated as a warning + fallback-to-full-video, not a validation failure. This allows new strategies to be added on the editor side before the worker is updated.

Recognised strategies: `"auto"`, `"first"`, `"last"`, `"longest"`, `"shortest"`, `"index:N"` (where N is a 0-based integer).  `"auto"` is an alias for `"longest"`.

**Decision: `trim` and `scene_clip_select` are not mutually exclusive — `scene_clip_select` wins**

If both are set, `scene_clip_select` is resolved to a `Trim` which overrides `layer.trim`. A warning is logged. This prevents silent mismatch when callers forget they have a manual trim set.

## Risks / Trade-offs

[Risk] Scene detection produces zero boundaries for a monotonous clip → `scene_clip_select` set but `scene_candidates[asset_id]` is empty or missing.
Mitigation: Fall back to the full video clip; emit a `SCENE_CLIP_SELECT_NO_CANDIDATES` warning. Never raise a `StageError`.

[Risk] `index:N` with N out of range.
Mitigation: Clamp to last available boundary; warn with actual vs requested index.

[Risk] `effective_trim` signature change to `build_layer_clip` is a semi-public API.
Mitigation: New parameter defaults to `None`; all existing callers continue to work with no changes.

[Risk] Very short detected scenes (< 0.5s) selected by `"shortest"` produce clips shorter than the scene duration, causing the scene to freeze on the last frame.
Mitigation: Document this limitation. The composer already handles `clip.duration < duration` gracefully by clamping — the freeze is from MoviePy's existing behaviour, not new behaviour.

## Migration Plan

No migration required. `scene_clip_select` defaults to `None`; all existing `RenderRequest` payloads parse identically. Schema is additive. No data store changes.
