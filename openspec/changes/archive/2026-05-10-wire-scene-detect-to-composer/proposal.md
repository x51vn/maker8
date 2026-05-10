## Why

`SceneDetectStage` already runs FFmpeg scene detection on video assets and stores `SceneBoundary` intervals in `ctx.scene_candidates` — but those boundaries are never passed to the composer. The feature is half-built: detection works, selection doesn't. Any job that enables `scene_detect_enabled: true` on an asset is doing real FFmpeg work whose output is silently discarded.

## What Changes

- Add `scene_clip_select` field to the `Layer` model in `render_contracts` so callers can declare how a detected scene boundary should be chosen for a video layer.
- Wire `ctx.scene_candidates` from `PipelineContext` into `RenderInput` so the composer has access to the detected boundaries.
- Add scene boundary selection resolution inside the composer (`_resolve_scene_trim`) that converts a `scene_clip_select` strategy string into an effective `Trim` applied to the video layer clip.
- Update the JSON schema artifacts (`docs/schemas/render_request.schema.json`) to reflect the new field.
- Add tests covering selection strategies and fallback behaviour.

## Capabilities

### New Capabilities

- `scene-clip-selection`: Allows a video layer to automatically select a clip segment from detected scene boundaries instead of playing the full asset. The layer declares a selection strategy (`"auto"`, `"first"`, `"last"`, `"longest"`, `"shortest"`, `"index:N"`). At render time the composer resolves the strategy against the detected boundaries for that asset and applies the resulting in/out trim. If no boundaries exist the layer falls back to the full asset with a warning.

### Modified Capabilities

<!-- none -->

## Impact

- `src/render_contracts/render_spec.py` — `Layer` model gains `scene_clip_select: str | None`
- `src/maker8/rendering/composer.py` — `RenderInput` gains `scene_candidates`; new `_resolve_scene_trim` function; applied in `_build_scene` before calling `build_layer_clip`
- `src/maker8/rendering/layers.py` — `build_layer_clip` / `_build_video` gains an `effective_trim` override parameter
- `src/maker8/pipeline/render.py` — `RenderInput` construction wires in `ctx.scene_candidates`
- `docs/schemas/render_request.schema.json` — regenerated after model change
- `tests/` — new unit tests for selection logic; existing tests unaffected (field defaults to `None`)
