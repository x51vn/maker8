## ADDED Requirements

### Requirement: Layer declares scene clip selection strategy
A video layer SHALL support an optional `scene_clip_select` field (string, default `null`). When set, the composer SHALL resolve the declared strategy against the detected scene boundaries for the layer's referenced asset and use the resulting time range as the effective trim for that layer's clip. When `scene_clip_select` is `null` (omitted), the layer's existing `trim` field is applied as before — no behaviour change.

#### Scenario: Layer with no scene_clip_select plays full asset
- **WHEN** a video layer has `scene_clip_select: null` (or field omitted)
- **THEN** the composer applies `layer.trim` exactly as before

#### Scenario: Layer with scene_clip_select and available boundaries selects a clip segment
- **WHEN** a video layer has `scene_clip_select: "longest"` and `scene_candidates[asset_id]` contains at least one `SceneBoundary`
- **THEN** the composer selects the boundary with the greatest `(end_sec - start_sec)`, converts it to a `Trim(in_=start_sec, out=end_sec)`, and applies it to the video clip in place of `layer.trim`

#### Scenario: scene_clip_select overrides an explicit trim
- **WHEN** a video layer has both `trim: {in: 5, out: 15}` and `scene_clip_select: "first"`
- **THEN** the composer uses the scene boundary trim and emits a `SCENE_CLIP_SELECT_TRIM_OVERRIDE` warning; `layer.trim` is ignored

---

### Requirement: Supported selection strategies
The composer SHALL recognise the following strategy strings for `scene_clip_select`:

| Strategy | Behaviour |
|---|---|
| `"auto"` | Alias for `"longest"` |
| `"first"` | First boundary in detected order |
| `"last"` | Last boundary in detected order |
| `"longest"` | Boundary with maximum duration |
| `"shortest"` | Boundary with minimum duration |
| `"index:N"` | Boundary at 0-based index N; if N ≥ count, clamp to last boundary and warn |

#### Scenario: auto selects longest
- **WHEN** `scene_clip_select: "auto"` and boundaries `[{0–3s}, {3–10s}, {10–12s}]`
- **THEN** the selected trim is `in=3, out=10` (longest is 7s)

#### Scenario: first selects earliest boundary
- **WHEN** `scene_clip_select: "first"` and boundaries `[{0–4s}, {4–9s}]`
- **THEN** the selected trim is `in=0, out=4`

#### Scenario: last selects final boundary
- **WHEN** `scene_clip_select: "last"` and boundaries `[{0–4s}, {4–9s}]`
- **THEN** the selected trim is `in=4, out=9`

#### Scenario: index:N with in-range index
- **WHEN** `scene_clip_select: "index:1"` and boundaries `[{0–3s}, {3–8s}, {8–12s}]`
- **THEN** the selected trim is `in=3, out=8`

#### Scenario: index:N with out-of-range index clamps to last
- **WHEN** `scene_clip_select: "index:99"` and boundaries `[{0–3s}, {3–8s}]`
- **THEN** the selected trim is `in=3, out=8` (last boundary) and a `SCENE_CLIP_SELECT_INDEX_OOB` warning is emitted

#### Scenario: unrecognised strategy falls back to full asset
- **WHEN** `scene_clip_select: "random"` (not in the recognised set)
- **THEN** the composer emits a `SCENE_CLIP_SELECT_UNKNOWN_STRATEGY` warning and plays the full asset

---

### Requirement: Graceful fallback when no scene candidates exist
When `scene_clip_select` is set but no scene boundaries are available for the asset (either scene detection was not enabled, detection found zero changes, or the asset failed detection), the layer SHALL fall back to playing the full asset (or `layer.trim` if set). A `SCENE_CLIP_SELECT_NO_CANDIDATES` warning SHALL be emitted. The job SHALL NOT fail.

#### Scenario: No candidates, plays full video
- **WHEN** a video layer has `scene_clip_select: "first"` but `scene_candidates` has no entry for the layer's asset
- **THEN** the full asset is played (subject to `layer.trim` if present) and a `SCENE_CLIP_SELECT_NO_CANDIDATES` warning is added to the job warnings

#### Scenario: scene_detect_enabled is false, scene_clip_select is set
- **WHEN** an asset has `scene_detect_enabled: false` and a layer referencing it has `scene_clip_select: "longest"`
- **THEN** no detection runs, no candidates exist, full asset plays with `SCENE_CLIP_SELECT_NO_CANDIDATES` warning

---

### Requirement: scene_candidates passed from pipeline context to composer
The `RenderInput` dataclass SHALL include a `scene_candidates` field (`dict[str, list[SceneBoundary]]`, default empty dict). `RenderStageImpl` SHALL populate it from `ctx.scene_candidates` when constructing `RenderInput`.

#### Scenario: Detected boundaries flow from context to composer
- **WHEN** `SceneDetectStage` populates `ctx.scene_candidates["asset-1"]` with two boundaries
- **THEN** `RenderInput.scene_candidates["asset-1"]` contains the same two boundaries at render time

#### Scenario: No scene detection, scene_candidates is empty
- **WHEN** no assets have `scene_detect_enabled: true`
- **THEN** `RenderInput.scene_candidates` is an empty dict and all layers behave identically to the pre-change behaviour
