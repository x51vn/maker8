## 1. Wire Model — Layer Field

- [x] 1.1 Read `src/render_contracts/render_spec.py` lines 123–149 to confirm current `Layer` fields
- [x] 1.2 Add `scene_clip_select: str | None = None` to the `Layer` model in `render_contracts/render_spec.py`
- [x] 1.3 Run `python scripts/generate_schemas.py` and confirm `scene_clip_select` appears in `docs/schemas/render_request.schema.json` under the `Layer` definition

## 2. RenderInput — scene_candidates Field

- [x] 2.1 Read `src/maker8/rendering/composer.py` lines 70–82 to confirm current `RenderInput` fields
- [x] 2.2 Add `scene_candidates: dict[str, list[SceneBoundary]] = field(default_factory=dict)` to `RenderInput` in `composer.py`; import `SceneBoundary` from `render_contracts.render_spec`
- [x] 2.3 Read `src/maker8/pipeline/render.py` lines 120–128 to confirm `RenderInput` construction
- [x] 2.4 Populate `scene_candidates=ctx.scene_candidates` in the `RenderInput(...)` constructor call in `render.py`

## 3. Selection Resolution Logic

- [x] 3.1 Add `_resolve_scene_trim(layer, scene_candidates)` function in `composer.py` that:
  - returns `None` when `layer.scene_clip_select` is `None`
  - returns `None` with a `SCENE_CLIP_SELECT_NO_CANDIDATES` warning appended to `ri.warnings` when `scene_candidates` has no entry for `layer.asset_ref`
  - resolves strategies `"auto"` / `"first"` / `"last"` / `"longest"` / `"shortest"` / `"index:N"` against the boundary list
  - clamps out-of-range `index:N` to last boundary with a `SCENE_CLIP_SELECT_INDEX_OOB` warning
  - returns `None` with a `SCENE_CLIP_SELECT_UNKNOWN_STRATEGY` warning for unrecognised strategies
  - warns `SCENE_CLIP_SELECT_TRIM_OVERRIDE` when both `layer.trim` and `scene_clip_select` are set
- [x] 3.2 Call `_resolve_scene_trim(layer, ri.scene_candidates)` inside `_build_scene` for each video layer before calling `build_layer_clip`, capturing the result as `effective_trim`

## 4. Effective Trim Override in Layer Builder

- [x] 4.1 Read `src/maker8/rendering/layers.py` lines 24–67 to confirm `build_layer_clip` and `_build_video` signatures
- [x] 4.2 Add `effective_trim: Trim | None = None` parameter to `build_layer_clip` and `_build_video`
- [x] 4.3 In `_build_video`, use `effective_trim` if not `None`, otherwise fall back to `layer.trim`
- [x] 4.4 Update the call site in `_build_scene` (and any other callers) to pass `effective_trim=effective_trim`

## 5. Tests

- [x] 5.1 Create `tests/test_scene_clip_selection.py` with unit tests for `_resolve_scene_trim` covering:
  - (a) `scene_clip_select=None` → returns `None`
  - (b) `"longest"` with three boundaries → selects widest interval
  - (c) `"auto"` → same result as `"longest"`
  - (d) `"first"` → selects first boundary
  - (e) `"last"` → selects last boundary
  - (f) `"shortest"` → selects narrowest interval
  - (g) `"index:1"` with 3 boundaries → selects second boundary
  - (h) `"index:99"` → clamps to last boundary, emits warning
  - (i) unrecognised strategy → emits warning, returns `None`
  - (j) `scene_clip_select` set but no candidates → emits warning, returns `None`
  - (k) both `trim` and `scene_clip_select` set → `scene_clip_select` wins, emits override warning
- [x] 5.2 Run `python -m pytest tests/test_scene_clip_selection.py` and confirm all tests pass
- [x] 5.3 Run `python -m pytest tests/` to confirm no regressions
