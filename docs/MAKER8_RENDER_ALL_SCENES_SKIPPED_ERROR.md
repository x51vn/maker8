# Render Failure: `ALL_SCENES_SKIPPED`

## Summary

This incident is a `RENDER`-stage failure in `maker8`, not a TTS failure and not a Dropbox upload failure.

The worker completed `TTS`, then entered `RENDER`, but every scene was skipped because no renderable layer content remained after degradation. The render stage therefore raised:

- `StageError`
- `error_code = ALL_SCENES_SKIPPED`
- `error_message = All scenes have no renderable content after degradation`

## What the logs show

For job `daea3e94-b817-4180-a88e-9abbed42fbc4`:

- `TTS` completed successfully
- `RENDER` started
- each scene emitted `render.scene.skipped`
- every skip used reason `all_layer_assets_missing`
- the render stage failed immediately with `ALL_SCENES_SKIPPED`
- the orchestrator marked the job as failed

Example skip messages:

- `scene_01` skipped
- `scene_02` skipped
- `scene_03` skipped
- `scene_04` skipped
- `scene_05` skipped

## Direct cause

The render stage only keeps scenes that have at least one usable layer:

- a text layer, or
- a layer whose `asset_ref` resolves to a downloaded asset path

If none of the layers in a scene satisfy that rule, the scene is skipped. If every scene is skipped, the stage fails with `ALL_SCENES_SKIPPED`.

## Most likely upstream causes

The failure usually means one of these happened earlier in the pipeline:

1. Asset resolution produced no usable assets.
2. Asset download succeeded partially but the downloaded files did not match the expected `asset_ref` values.
3. The render request contains scenes without text layers and without valid visual assets.
4. An upstream degradation step removed the only renderable content from each scene.

## Where to inspect

Check these places first:

- [src/maker8/pipeline/render.py](../src/maker8/pipeline/render.py)
- [src/maker8/pipeline/resolve.py](../src/maker8/pipeline/resolve.py)
- [src/maker8/pipeline/download.py](../src/maker8/pipeline/download.py)
- the rendered request payload produced by editor8 for the job

## Practical interpretation

This is not a transient render crash. It means the pipeline reached render time with no surviving content to compose into a video.

The correct next step is to trace why the render spec did not end up with usable scene content, not to retry the job blindly.

## Fix applied

**Root cause:** editor8's assembler (`pipeline/assembler.py`) created scenes with **empty `layers`** when no asset candidates were found. maker8's render stage considers a scene viable only if it has a text layer or a layer with a valid `asset_ref`. Empty layers → scene skipped → all scenes skipped → `ALL_SCENES_SKIPPED`.

**Change:** When no asset candidate exists for a scene, the assembler now adds a **fallback text layer** containing the scene's narration text. This guarantees every scene has at least one renderable layer (`type="text"`) that passes maker8's viability check.

Files changed:
- `editor8/backend/src/editor8/pipeline/assembler.py` — added text layer fallback in `assemble_render_request()`
- `editor8/backend/tests/test_assembler.py` — updated `test_scene_without_assets` → `test_scene_without_assets_gets_text_fallback`
