# CONTRACT_FIELD_STATUS.md

> Field-level status for the **`video.render.request.v1`** wire format.
> Canonical models live in `render_contracts/render_spec.py`.
> Both **editor8** (producer) and **maker8** (consumer) import from it.

## Legend

| Status | Meaning |
|--------|---------|
| **ACTIVE** | Field is read and acted upon by maker8 rendering pipeline |
| **PASS-THROUGH** | Field is stored in context / forwarded to result but not interpreted |
| **RESERVED** | Field exists in schema but is not consumed — reserved for future use |

---

## RenderRequest (top-level)

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `job_id` | `str` | **ACTIVE** | Pipeline context key, file naming |
| `spec_version` | `str` | **ACTIVE** | Validated against supported versions |
| `render_spec` | `RenderSpec` | **ACTIVE** | Core pipeline input |
| `result` | `ResultDestination` | **PASS-THROUGH** | Stored in context; individual fields unused (config-driven) |
| `trace` | `Trace` | **PASS-THROUGH** | Stored in context for correlation tracing |

## ResultDestination

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `type` | `str` | **RESERVED** | Always "kafka"; hardcoded in pipeline |
| `topic` | `str` | **RESERVED** | Config-driven via `kafka_render_result_topic` |
| `key` | `str` | **RESERVED** | `job_id` used as Kafka key instead |

## Trace

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `correlation_id` | `str` | **PASS-THROUGH** | Forwarded to result; not analyzed |

---

## RenderSpec

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `spec_version` | `str` | **ACTIVE** | Checked against `_SUPPORTED_SPEC_VERSIONS` |
| `canvas` | `Canvas` | **ACTIVE** | Dimensions, FPS, background for composition |
| `defaults` | `Defaults` | **ACTIVE** | Fallback narration + scene timing |
| `assets` | `list[Asset]` | **ACTIVE** | Resolved, downloaded, normalized |
| `scenes` | `list[Scene]` | **ACTIVE** | Main rendering loop |
| `output` | `OutputConfig` | **ACTIVE** | FFmpeg encode parameters |
| `publish` | `PublishConfig` | **ACTIVE** | Targets forwarded to result |

## Canvas

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `w` | `int` | **ACTIVE** | Video width; validated > 0 |
| `h` | `int` | **ACTIVE** | Video height; validated > 0 |
| `fps` | `int` | **ACTIVE** | Frame rate; validated 1–120 |
| `bg` | `str` | **ACTIVE** | Background color (hex → RGB) |
| `safe_area` | `SafeArea?` | **RESERVED** | Defined but never consumed |

## SafeArea

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `top` | `int` | **RESERVED** | — |
| `right` | `int` | **RESERVED** | — |
| `bottom` | `int` | **RESERVED** | — |
| `left` | `int` | **RESERVED** | — |

## Defaults

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `narration` | `NarrationDefaults` | **ACTIVE** | Fallback TTS config |
| `scene_timing` | `SceneTiming` | **ACTIVE** | Duration padding |

## NarrationDefaults

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `lang` | `str` | **ACTIVE** | Fallback language for TTS |
| `tts_preset_ref` | `str` | **ACTIVE** | Fallback TTS preset |

## SceneTiming

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `head_pad_sec` | `float` | **ACTIVE** | Padding before narration |
| `tail_pad_sec` | `float` | **ACTIVE** | Padding after narration |
| `duration_mode` | `str` | **RESERVED** | Always "auto_from_tts"; not branched on |

---

## Asset

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `id` | `str` | **ACTIVE** | Registry key for layer asset_ref lookup |
| `type` | `str` | **ACTIVE** | "video" / "image" / "audio" — normalize dispatch |
| `source` | `AssetSource` | **ACTIVE** | Plugin resolution input |

## AssetSource

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `kind` | `str` | **ACTIVE** | Plugin dispatch ("youtube", "http") |
| `url` | `str` | **ACTIVE** | Passed to plugin.resolve() |
| `options` | `AssetSourceOptions` | **RESERVED** | Object passed but fields never inspected |

## AssetSourceOptions

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `format` | `str?` | **RESERVED** | Future: select download format |
| `max_duration_sec` | `int?` | **RESERVED** | Future: limit download duration |

---

## Scene

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `scene_id` | `str` | **ACTIVE** | TTS mapping key, validation, logging |
| `duration` | `float?` | **ACTIVE** | Explicit duration override (else auto) |
| `narration` | `SceneNarration` | **ACTIVE** | TTS input text + overrides |
| `layers` | `list[Layer]` | **ACTIVE** | Composited per-scene |
| `audio_tracks` | `list[AudioTrack]` | **ACTIVE** | Mixed into scene audio |
| `effects` | `list[EffectInstance]` | **ACTIVE** | Applied via plugin registry |
| `transition_out` | `Transition?` | **ACTIVE** | Duration used in scene timing |

## SceneNarration

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `text` | `str` | **ACTIVE** | Required; TTS input |
| `lang` | `str?` | **ACTIVE** | Scene-level language override |
| `tts_preset_ref` | `str?` | **ACTIVE** | Scene-level TTS preset override |

## Transition

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `type` | `str` | **RESERVED** | Always crossfade; not branched on |
| `duration` | `float` | **ACTIVE** | Used in scene duration calculations |

---

## Layer

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `layer_id` | `str` | **ACTIVE** | Validation, error messages |
| `type` | `Literal["image","video","text"]` | **ACTIVE** | Dispatch to render handler |
| `rect` | `Rect` | **ACTIVE** | Position + dimensions |
| `anchor` | `str` | **ACTIVE** | Positioning mode |
| `opacity` | `float` | **ACTIVE** | `with_opacity()` effect |
| `rotation_deg` | `float` | **ACTIVE** | `rotated()` effect |
| `scale` | `float` | **ACTIVE** | Dimension scaling |
| `asset_ref` | `str?` | **ACTIVE** | Asset lookup for image/video layers |
| `fit` | `str?` | **ACTIVE** | "cover" / "contain" fit mode |
| `align` | `str?` | **RESERVED** | Defined but not consumed; rect handles positioning |
| `trim` | `Trim?` | **ACTIVE** | Video subclipping |
| `text` | `str?` | **ACTIVE** | Content for text layers |
| `text_align` | `str?` | **ACTIVE** | Horizontal text alignment |
| `valign` | `str?` | **ACTIVE** | Vertical text alignment |
| `style` | `TextStyle?` | **ACTIVE** | Font, color, stroke config |

## Rect

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `x` | `int` | **ACTIVE** | X position with anchor logic |
| `y` | `int` | **ACTIVE** | Y position with anchor logic |
| `w` | `int` | **ACTIVE** | Width for fit/scale calculations |
| `h` | `int` | **ACTIVE** | Height for fit/scale calculations |

## Trim

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `in_` (wire: `"in"`) | `float` | **ACTIVE** | Subclip start time |
| `out` | `float` | **ACTIVE** | Subclip end time |

## TextStyle

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `font_ref` | `str` | **ACTIVE** | Font loading via `_load_font()` |
| `size` | `int` | **ACTIVE** | Font size in pixels |
| `color` | `str` | **ACTIVE** | Fill color (hex → RGBA) |
| `stroke_color` | `str?` | **ACTIVE** | Stroke outline color |
| `stroke_width` | `int` | **ACTIVE** | Stroke width in Pillow draw |
| `line_height` | `float` | **ACTIVE** | Line spacing multiplier |
| `wrap` | `bool` | **ACTIVE** | Text wrapping toggle |

---

## AudioTrack

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `asset_ref` | `str` | **ACTIVE** | Lookup audio file from asset_paths |
| `trim` | `Trim?` | **ACTIVE** | Audio subclipping |
| `volume` | `float` | **ACTIVE** | `MultiplyVolume` effect |
| `loop` | `bool` | **ACTIVE** | Loop to scene duration |

## EffectInstance

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `plugin_id` | `str` | **ACTIVE** | Plugin registry lookup |
| `params` | `dict[str, Any]` | **ACTIVE** | Passed to `effect.apply()` via `model_dump()` |

---

## OutputConfig

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `codec` | `str` | **ACTIVE** | FFmpeg video codec |
| `audio_codec` | `str` | **ACTIVE** | FFmpeg audio codec |
| `bitrate` | `str` | **ACTIVE** | Video bitrate |
| `audio_bitrate` | `str` | **ACTIVE** | Audio bitrate |
| `preset` | `str` | **ACTIVE** | FFmpeg encoding preset |
| `pix_fmt` | `str` | **ACTIVE** | Pixel format |

## PublishTarget

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `platform` | `str` | **ACTIVE** | Passed to result, used in canonicalization |
| `account_ref` | `str` | **ACTIVE** | Passed to result, used in canonicalization |
| `metadata` | `dict[str, Any]` | **RESERVED** | Future: platform-specific metadata |
| `params` | `dict[str, Any]` | **RESERVED** | Future: platform-specific params |

## PublishConfig

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `targets` | `list[PublishTarget]` | **ACTIVE** | Forwarded to render result |

---

## Summary

| Status | Count | Percentage |
|--------|-------|------------|
| **ACTIVE** | ~52 | 69% |
| **PASS-THROUGH** | ~3 | 4% |
| **RESERVED** | ~20 | 27% |

### Reserved fields (not yet consumed by maker8)

1. `Canvas.safe_area` — future: constrain layer placement
2. `SceneTiming.duration_mode` — future: support fixed/manual duration modes
3. `AssetSourceOptions.format` — future: format selection in download
4. `AssetSourceOptions.max_duration_sec` — future: download duration limits
5. `Layer.align` — future: alignment within rect (currently handled by anchor/rect)
6. `Transition.type` — future: support fade, wipe, etc. (currently always crossfade)
7. `PublishTarget.metadata` — future: platform-specific title/description
8. `PublishTarget.params` — future: platform-specific upload params
9. `ResultDestination.type` — future: support non-Kafka result delivery
10. `ResultDestination.topic` — future: dynamic topic routing
11. `ResultDestination.key` — future: custom Kafka key (currently job_id)
12. `SafeArea.*` — all 4 inset fields reserved
