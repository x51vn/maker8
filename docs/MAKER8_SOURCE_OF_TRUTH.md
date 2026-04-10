# Maker8 – Source of Truth

> **Investigation date**: 2026-04-05
> **Baseline**: Current `main` branch, all code files as of investigation date
> **Scope**: Go-live readiness for `maker8` video render pipeline serving news video production

---

## Table of Contents

1. [Purpose & Scope](#1-purpose--scope)
2. [Business Use Case](#2-business-use-case)
3. [System Context & Boundaries](#3-system-context--boundaries)
4. [Runtime Architecture](#4-runtime-architecture)
5. [Input Contract: `video.render.request.v1`](#5-input-contract-videorenderrequestv1)
6. [Output Contract: `video.render.result.v1`](#6-output-contract-videorenderresultv1)
7. [DLQ Contract: `video.render.dlq.v1`](#7-dlq-contract-videorenderdlqv1)
8. [Field Status Matrix](#8-field-status-matrix)
9. [Pipeline Stage Semantics](#9-pipeline-stage-semantics)
10. [External Dependencies & Configuration](#10-external-dependencies--configuration)
11. [Failure Handling, Retry, Degraded Mode, DLQ](#11-failure-handling-retry-degraded-mode-dlq)
12. [Drift Register](#12-drift-register)
13. [Gap Register](#13-gap-register)
14. [Go-Live Blocker List](#14-go-live-blocker-list)
15. [Production Readiness Matrix](#15-production-readiness-matrix)
16. [Editorial, Attribution, Compliance Constraints](#16-editorial-attribution-compliance-constraints)
17. [Known Limitations & Reserved Fields](#17-known-limitations--reserved-fields)
18. [Decision Log](#18-decision-log)
19. [Document Disposition](#19-document-disposition)
20. [Go-Live Conclusion](#20-go-live-conclusion)

---

## 1. Purpose & Scope

`maker8` is a **video render worker** that:

1. Consumes `RenderRequest` messages from Kafka topic `video.render.request.v1`
2. Validates, downloads assets, synthesizes TTS narration, composes video via MoviePy/FFmpeg
3. Uploads `.mp4` + manifest to Dropbox
4. Emits `RenderResult` to `video.render.result.v1` (or DLQ on failure)

This document is the **single authoritative narrative** for the maker8 system. It replaces all prior scattered documentation as the primary reference.

**Canonical contract models** live in `src/render_contracts/render_spec.py` — shared identically between editor8 and maker8.

---

## 2. Business Use Case

The system produces **news videos** for automated publishing.

Implications for go-live:

- Output must be factually aligned with the news content provided
- Source attribution and provenance must be traceable
- Latency from request to result must meet editorial publishing cadence
- Degraded outputs (missing assets, failed TTS) may or may not be acceptable depending on editorial policy
- Copyright and usage rights of downloaded assets are upstream concerns (editor8/operator responsibility), but maker8 must preserve attribution metadata for audit

---

## 3. System Context & Boundaries

```
editor8 (producer)
  → Kafka: video.render.request.v1
    → maker8 (render worker)
      → Kafka: video.render.result.v1
      → Kafka: video.render.dlq.v1
      → Dropbox: /renders/<yyyy>/<mm>/<dd>/<job_id>.mp4
      → Dropbox: /renders/<yyyy>/<mm>/<dd>/<job_id>.manifest.json
```

### Boundary ownership

| Boundary | Owner | Notes |
|----------|-------|-------|
| `RenderRequest` schema | Shared (render_contracts) | Single source: `render_contracts/render_spec.py` |
| `RenderResult` schema | maker8 | Defined in `maker8/models/contracts.py` |
| `DLQPayload` schema | maker8 | Defined in `maker8/models/contracts.py` |
| Kafka topic config | Infrastructure/DevOps | Topics, retention, partitions |
| Dropbox path convention | maker8 | `/renders/<date>/<job_id>.*` |
| Asset content/rights | editor8 / Operator | maker8 downloads what it's told |
| TTS credentials | Operator / DevOps | Provisioned externally, rotated by maker8 |

### Contract versioning

- `render_contracts` package is embedded (copied) in both repos
- **Fact**: Both copies are **identical** as of investigation date (verified via `diff`)
- Version sync is manual — no package registry or CI check enforces sync

---

## 4. Runtime Architecture

### Concurrency model

- **1 instance = 1 job at a time** (synchronous, blocking pipeline)
- Throughput scales by adding worker instances
- A long-running job monopolizes its instance

### Pipeline flow

```
VALIDATE → RESOLVE_ASSETS → DOWNLOAD → NORMALIZE → TTS → RENDER → UPLOAD_DROPBOX → EMIT_RESULT
```

### Key runtime characteristics

- Per-job work directory: `MAKER8_WORK_DIR/<job_id>/` (mode 0o700)
- All intermediate artifacts on local disk
- Work directory cleaned up after job completion (best-effort)
- Consumer uses manual commit after handler returns (always commits, see Drift D-001)
- Producer flushes after every message (blocking)
- Shutdown uses `os._exit(0)` to avoid librdkafka/MoviePy segfaults

### Health probes

| File | Purpose |
|------|---------|
| `/tmp/maker8_live` | Liveness: process running |
| `/tmp/maker8_ready` | Readiness: all dependencies initialized |
| `/tmp/maker8_status.json` | Full runtime snapshot (JSON) |

---

## 5. Input Contract: `video.render.request.v1`

**Canonical model**: `render_contracts.render_spec.RenderRequest`

```python
class RenderRequest(BaseModel):
    job_id: str                              # ACTIVE – pipeline key
    spec_version: str = "1.0"                # ACTIVE – validated
    render_spec: RenderSpec                   # ACTIVE – core input
    dry_run: bool = False                     # ACTIVE – forwarded to result
    canvas_profile: str | None = None         # ACTIVE – forwarded to result
    publish_intent: str = "render_only"       # PASS-THROUGH – not interpreted by maker8
    uploader_metadata: UploaderMetadata       # ACTIVE – forwarded to result/manifest
    result: ResultDestination                 # ACTIVE – used for topic/key routing
    trace: Trace                              # PASS-THROUGH – forwarded for correlation
```

### Fields added since original specs but missing from schemas/examples

- `dry_run` — **not in** JSON Schema, not in any example
- `canvas_profile` — **not in** JSON Schema, not in any example
- `publish_intent` — **not in** JSON Schema, not in any example
- `uploader_metadata` — **not in** JSON Schema, not in any example

These are all documented as drift items (see D-003).

---

## 6. Output Contract: `video.render.result.v1`

**Canonical model**: `maker8.models.contracts.RenderResult`

```python
class RenderResult(BaseModel):
    job_id: str                              # Always present
    status: JobStatus                        # DONE | FAILED | PARTIAL
    job_key: str = ""                        # sha256 hash of canonical spec
    dry_run: bool = False                    # Forwarded from request
    canvas_profile: str | None = None        # Forwarded from request
    dropbox: DropboxOutput                   # Video + manifest refs
    output_meta: OutputMeta                  # Duration, resolution, size
    uploader_metadata: UploaderMetadata      # Forwarded from request
    publish_targets: list[PublishTarget]      # From render_spec.publish.targets
    asset_report: list[dict]                 # Per-asset download stats
    warnings: list[AssetWarning]             # Degradation warnings
    engine_versions: EngineVersions          # moviepy/ffmpeg/yt-dlp versions
    trace: Trace                             # Forwarded for correlation
    error: ErrorInfo | None = None           # Present on FAILED
```

### Status semantics

| Status | Meaning |
|--------|---------|
| `DONE` | All scenes rendered, all assets resolved, no warnings |
| `PARTIAL` | Job completed but with degradation (missing assets, failed TTS for some scenes) |
| `FAILED` | Pipeline failed at some stage; error details in `error` field |

---

## 7. DLQ Contract: `video.render.dlq.v1`

**Canonical model**: `maker8.models.contracts.DLQPayload`

```python
class DLQPayload(BaseModel):
    job_id: str
    job_key: str = ""
    failed_stage: str                        # RenderStage enum value
    attempts: int
    max_attempts: int = 0
    last_error: ErrorInfo | None = None
    dropbox: dict[str, Any] = {}             # Partial dropbox refs if available
    trace: Trace                             # Forwarded for correlation
    debug_context: dict[str, Any] = {}       # Diagnostic info for operator
```

DLQ is emitted when:
1. A non-retryable stage fails
2. All retry attempts are exhausted for a retryable stage
3. Invalid payload cannot be parsed as `RenderRequest`

---

## 8. Field Status Matrix

> Source of truth: runtime code analysis cross-referenced against `CONTRACT_FIELD_STATUS.md`

### RenderRequest (top-level)

| Field | Status | Evidence | Notes |
|-------|--------|----------|-------|
| `job_id` | **ACTIVE** | `orchestrator.py:handle()` | Pipeline key, file naming, Kafka key |
| `spec_version` | **ACTIVE** | `validate.py` | Checked against `{"1.0"}` |
| `render_spec` | **ACTIVE** | All stages | Core pipeline input |
| `dry_run` | **ACTIVE** | `context.py`, `emit.py`, `upload.py` | Forwarded to result and manifest |
| `canvas_profile` | **ACTIVE** | `context.py`, `emit.py` | Forwarded to result and manifest |
| `publish_intent` | **PASS-THROUGH** | Not read by any stage | Exists in schema, not consumed |
| `uploader_metadata` | **ACTIVE** | `context.py`, `emit.py`, `upload.py` | Forwarded to result and manifest |
| `result` | **ACTIVE** | `emit.py`, `orchestrator.py` | Topic/key used for routing (see D-002) |
| `trace` | **PASS-THROUGH** | `context.py`, `emit.py` | Forwarded to result; `correlation_id` logged |

### ResultDestination

| Field | Status | Evidence | Notes |
|-------|--------|----------|-------|
| `type` | **RESERVED** | Never read | Always `"kafka"` |
| `topic` | **ACTIVE** | `emit.py:_resolve_topic()`, `orchestrator.py:_send_failed_result()` | ⚠️ Used for routing — contradicts CONTRACT_FIELD_STATUS.md |
| `key` | **ACTIVE** | `emit.py:execute()`, `orchestrator.py:_send_failed_result()` | ⚠️ Used as Kafka key — contradicts CONTRACT_FIELD_STATUS.md |

### Canvas

| Field | Status | Evidence |
|-------|--------|----------|
| `w` | **ACTIVE** | `validate.py`, `rendering/composer.py` |
| `h` | **ACTIVE** | `validate.py`, `rendering/composer.py` |
| `fps` | **ACTIVE** | `validate.py`, `rendering/composer.py` |
| `bg` | **ACTIVE** | `rendering/composer.py` |
| `safe_area` | **RESERVED** | Never consumed by any stage |

### SceneTiming

| Field | Status | Evidence |
|-------|--------|----------|
| `head_pad_sec` | **ACTIVE** | `rendering/composer.py` |
| `tail_pad_sec` | **ACTIVE** | `rendering/composer.py` |
| `duration_mode` | **RESERVED** | Never branched on; always `auto_from_tts` |

### AssetSourceOptions

| Field | Status | Evidence |
|-------|--------|----------|
| `format` | **ACTIVE** | `plugins/sources/youtube.py` — used by yt-dlp connector |
| `max_duration_sec` | **ACTIVE** | `plugins/sources/youtube.py` — filters out videos exceeding this limit (seconds); defaults to 600 |

> ⚠️ `AssetSourceOptions.format` is marked RESERVED in `CONTRACT_FIELD_STATUS.md` but is **ACTIVE** in runtime. This is drift D-004.

### Layer

| Field | Status | Evidence |
|-------|--------|----------|
| `layer_id` | **ACTIVE** | Validation, error messages |
| `type` | **ACTIVE** | Dispatch to render handler |
| `rect` | **ACTIVE** | Position + dimensions |
| `anchor` | **ACTIVE** | Positioning mode |
| `opacity` | **ACTIVE** | `with_opacity()` effect |
| `rotation_deg` | **ACTIVE** | `rotated()` effect |
| `scale` | **ACTIVE** | Dimension scaling |
| `asset_ref` | **ACTIVE** | Asset lookup |
| `fit` | **ACTIVE** | "cover"/"contain" fit mode |
| `align` | **RESERVED** | Not consumed; `rect` handles positioning |
| `trim` | **ACTIVE** | Video subclipping |
| `text` | **ACTIVE** | Content for text layers |
| `text_align` | **ACTIVE** | Horizontal text alignment |
| `valign` | **ACTIVE** | Vertical text alignment |
| `style` | **ACTIVE** | Font, color, stroke config |

### Transition

| Field | Status | Evidence |
|-------|--------|----------|
| `type` | **RESERVED** | Always crossfade; not branched on |
| `duration` | **ACTIVE** | Used in scene duration calculations |

### PublishTarget

| Field | Status | Evidence |
|-------|--------|----------|
| `platform` | **ACTIVE** | Canonicalization, forwarded to result |
| `account_ref` | **ACTIVE** | Canonicalization, forwarded to result |
| `variant` | **PASS-THROUGH** | Not consumed |
| `enabled` | **PASS-THROUGH** | Not consumed |
| `metadata` | **RESERVED** | Not consumed by maker8 |
| `params` | **RESERVED** | Not consumed by maker8 |

(All other nested fields — Trim, Rect, TextStyle, AudioTrack, EffectInstance, OutputConfig, NarrationDefaults — are **ACTIVE** as documented in `CONTRACT_FIELD_STATUS.md` and verified against rendering code.)

---

## 9. Pipeline Stage Semantics

### Stage 1: VALIDATE

| Attribute | Value |
|-----------|-------|
| **Input** | `PipelineContext.render_spec` (from `RenderRequest`) |
| **Output** | `ctx.job_key` computed |
| **Reads from context** | `render_spec` |
| **Writes to context** | `job_key` |
| **Retryable** | No |
| **Failure codes** | `UNSUPPORTED_SPEC_VERSION`, `INVALID_CANVAS`, `NO_SCENES`, `DUPLICATE_SCENE_ID`, `DUPLICATE_ASSET_ID`, `EMPTY_NARRATION`, `UNKNOWN_ASSET_REF` |
| **Validation rules** | spec_version ∈ {"1.0"}, canvas w>0 h>0 fps>0, ≥1 scene, unique scene_ids, unique asset_ids, non-empty narration.text, all asset_refs resolve |

### Stage 2: RESOLVE_ASSETS

| Attribute | Value |
|-----------|-------|
| **Input** | `render_spec.assets[]` |
| **Output** | `ctx.resolved_plans` |
| **Reads from context** | `render_spec.assets`, plugin registry |
| **Writes to context** | `resolved_plans[asset_id] = ResolvedAssetPlan` |
| **Retryable** | Yes |
| **Failure codes** | `UNSUPPORTED_SOURCE`, `INVALID_SOURCE_URL`, `INVALID_YTDLP_FORMAT`, `INVALID_SOURCE_OPTIONS`, `INVALID_SOURCE_CONFIG`, `RESOLVE_FAILED` |
| **Notes** | ValueError → non-retryable; other exceptions → retryable |

### Stage 3: DOWNLOAD

| Attribute | Value |
|-----------|-------|
| **Input** | `ctx.resolved_plans` |
| **Output** | `ctx.downloaded_assets` |
| **Reads from context** | `resolved_plans`, `failed_assets`, `downloaded_assets` (idempotent) |
| **Writes to context** | `downloaded_assets[asset_id] = Path`, `failed_assets`, `warnings`, `asset_report` |
| **Retryable** | Yes |
| **Failure codes** | `DISK_SPACE_LOW` (pre-flight check, retryable) |
| **Degradation** | Per-asset failure is isolated: asset marked failed, warning added, pipeline continues |
| **Pre-flight** | Checks ≥500MB free disk space |

### Stage 4: NORMALIZE

| Attribute | Value |
|-----------|-------|
| **Input** | `ctx.downloaded_assets` |
| **Output** | `ctx.normalized_assets` |
| **Reads from context** | `downloaded_assets`, `render_spec.assets` (for type info) |
| **Writes to context** | `normalized_assets[asset_id] = Path`, `warnings` |
| **Retryable** | Yes (was No in README — drift D-005) |
| **Failure codes** | `NORMALIZE_KILLED` (external SIGKILL), `NORMALIZE_FAILED`, `NORMALIZE_TIMEOUT` |
| **Notes** | Validates output with `_is_valid_media()` (size + ffprobe); purges corrupt artifacts |

### Stage 5: TTS

| Attribute | Value |
|-----------|-------|
| **Input** | `render_spec.scenes[].narration` |
| **Output** | `ctx.tts_results` |
| **Reads from context** | `render_spec.scenes`, `render_spec.defaults.narration`, `tts_results` (idempotent), `skipped_scenes` |
| **Writes to context** | `tts_results[scene_id] = TTSResult`, `warnings` |
| **Retryable** | Yes |
| **Failure codes** | `TTS_FAILED`, `TTS_TIMEOUT` |
| **Degradation** | Per-scene failure isolated: scene renders without narration |
| **Credential rotation** | One credential set per video; round-robin across videos |

### Stage 6: RENDER

| Attribute | Value |
|-----------|-------|
| **Input** | normalized/downloaded assets, TTS results, render_spec |
| **Output** | `ctx.rendered_video`, `ctx.output_meta` |
| **Reads from context** | `downloaded_assets`, `normalized_assets`, `tts_results`, `render_spec`, `skipped_scenes`, `failed_assets` |
| **Writes to context** | `rendered_video = Path`, `output_meta = OutputMeta`, `skipped_scenes`, `warnings` |
| **Retryable** | No |
| **Failure codes** | `ALL_SCENES_SKIPPED`, `RENDER_TIMEOUT`, `RENDER_FAILED` |
| **Scene filtering** | Scenes with all layer assets missing are skipped; if all scenes skipped → fail |

### Stage 7: UPLOAD_DROPBOX

| Attribute | Value |
|-----------|-------|
| **Input** | `ctx.rendered_video`, `ctx.output_dir` |
| **Output** | `ctx.dropbox_video_ref`, `ctx.dropbox_manifest_ref` |
| **Reads from context** | `rendered_video`, `job_id`, `job_key`, all metadata for manifest |
| **Writes to context** | `dropbox_video_ref`, `dropbox_manifest_ref` |
| **Retryable** | Yes |
| **Failure codes** | `NO_VIDEO`, `AUTH_FAILED` (non-retryable), `INVALID_CONFIG` (non-retryable), `RATE_LIMITED`, `SERVER_ERROR`, `API_ERROR`, `UPLOAD_FAILED` |
| **Notes** | Uploads video then manifest; error handling per Dropbox exception type |

### Stage 8: EMIT_RESULT

| Attribute | Value |
|-----------|-------|
| **Input** | All context fields |
| **Output** | Kafka message to result topic |
| **Reads from context** | Everything (builds `RenderResult`) |
| **Writes to context** | None |
| **Retryable** | Yes |
| **Failure codes** | `EMIT_FAILED` |
| **Routing** | Uses `result_destination.topic` if set, else config default; uses `result_destination.key` if set, else `job_id` |

---

## 10. External Dependencies & Configuration

### Dependencies

| Dependency | Purpose | Failure impact | Retryable |
|------------|---------|---------------|-----------|
| **Kafka** | Input/output messaging | Fatal — cannot receive or emit | N/A |
| **FFmpeg** | Media normalization, encoding | NORMALIZE/RENDER fail | Stage-dependent |
| **yt-dlp** | YouTube asset download | RESOLVE/DOWNLOAD fail | Yes |
| **Dropbox** | Output storage | UPLOAD fail | Yes (except auth) |
| **Google Cloud TTS** | Narration synthesis | TTS fail | Yes |
| **ElevenLabs TTS** | Narration synthesis | TTS fail | Yes |
| **gTTS** | Fallback TTS | TTS fail | Yes |
| **Local disk** | Work directory, artifacts | All stages fail | Check: 500MB min |

### Configuration (env prefix: `MAKER8_`)

| Category | Key variables |
|----------|---------------|
| Kafka | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_GROUP_ID`, `KAFKA_USERNAME`, `KAFKA_PASSWORD`, `KAFKA_SECURITY_PROTOCOL`, `KAFKA_SASL_MECHANISM`, `KAFKA_MAX_POLL_INTERVAL_MS` (30min) |
| Dropbox | `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN` |
| TTS | `TTS_PRESETS_PATH`, `GOOGLE_TTS_KEYS_DIR`, `ELEVENLABS_KEYS_DIR`, `ELEVENLABS_API_KEY`, `TTS_TIMEOUT_SEC` (120s) |
| Pipeline | `WORK_DIR` (/tmp/maker8), `RENDER_MAX_ATTEMPTS` (5), `RENDER_RETRY_MIN_DELAY_SEC` (60), `RENDER_RETRY_MAX_DELAY_SEC` (21600) |
| Performance | `PERF_MODE` (balanced), `PROXY_MAX_RESOLUTION` |
| Observability | `LOG_LEVEL`, `LOG_FORMAT`, `METRICS_ENABLED`, `METRICS_PORT`, `STATUS_FILE` |

### Startup validation

| Check | Behavior if missing |
|-------|--------------------|
| TTS provider | `os._exit(1)` — cannot start |
| Dropbox credentials | `os._exit(1)` — cannot start |
| FFmpeg | Implicit — crash at NORMALIZE/RENDER |
| Kafka | Implicit — consumer hangs on connect |

---

## 11. Failure Handling, Retry, Degraded Mode, DLQ

### Retry policy

- **Max attempts**: 5 (configurable)
- **Backoff**: Exponential, 60s → 21,600s (6h), ±10% jitter
- **Sleep**: Blocks current thread (worker idle during retry sleep)

### Retryable stages

| Stage | Retryable | Evidence |
|-------|-----------|----------|
| VALIDATE | No | `retry.py`: not in `RENDER_RETRYABLE_STAGES` |
| RESOLVE_ASSETS | Yes | `retry.py` |
| DOWNLOAD | Yes | `retry.py` |
| NORMALIZE | **Yes** | `retry.py` — ⚠️ README says No (drift D-005) |
| TTS | Yes | `retry.py` |
| RENDER | No | `retry.py`: not in `RENDER_RETRYABLE_STAGES` |
| UPLOAD_DROPBOX | Yes | `retry.py` |
| EMIT_RESULT | Yes | `retry.py` |

### Degradation model

- **Asset-level**: Failed downloads → asset marked in `failed_assets`, warning added, pipeline continues
- **Scene-level**: Scenes with all layer assets missing → skipped, warning added
- **TTS-level**: Failed TTS → scene renders without narration, warning added
- **Job-level**: If all scenes are skipped → `ALL_SCENES_SKIPPED` (non-retryable fail)
- **Status**: Any warning → `PARTIAL` instead of `DONE`

### DLQ emission

DLQ is emitted for:
1. Retryable stage with exhausted retries
2. Non-retryable stage failure
3. Invalid payload that cannot parse as `RenderRequest`

DLQ includes `debug_context` with:
- Partial asset report
- Resolved/downloaded/TTS scene IDs
- Failed assets and skipped scenes
- Warning count

---

## 12. Drift Register

| ID | Type | Surface | Evidence A | Evidence B | Impact | Severity | Decision |
|----|------|---------|------------|------------|--------|----------|----------|
| D-001 | Doc drift | Consumer commit | `consumer.py` docstring says "manual commit after **successful** handling" | Code commits **always** after handler returns (success or failure) | Docs mislead about at-least-once semantics | **Medium** | Code behavior is actually correct (orchestrator handles DLQ); update docstring |
| D-002 | Doc drift | ResultDestination | `CONTRACT_FIELD_STATUS.md` marks `result.topic` and `result.key` as **RESERVED** | `emit.py:_resolve_topic()` and `orchestrator.py:_send_failed_result()` **actively use** both fields | Operator/dev thinks fields are ignored but they control routing | **High** | Update CONTRACT_FIELD_STATUS.md: both fields are **ACTIVE** |
| D-003 | Schema/example gap | New top-level fields | `render_contracts/render_spec.py` has `dry_run`, `canvas_profile`, `publish_intent`, `uploader_metadata` | `docs/schemas/render_request.schema.json` and all examples lack these fields | Schema and examples are stale; integrators may miss required fields | **High** | Regenerate JSON schemas from Pydantic models; update all examples |
| D-004 | Doc drift | AssetSourceOptions | `CONTRACT_FIELD_STATUS.md` marks `AssetSourceOptions.format` as **RESERVED** | `plugins/sources/youtube.py` actively passes `format` to yt-dlp | Dev/PO thinks format is unused but it controls download quality | **High** | Update CONTRACT_FIELD_STATUS.md: `format` is **ACTIVE** |
| D-005 | Doc drift | NORMALIZE retryability | `README.md` Pipeline Stages table says NORMALIZE is **No** (not retryable) | `retry.py:RENDER_RETRYABLE_STAGES` includes `RenderStage.NORMALIZE` | Operator may not expect NORMALIZE retries | **Medium** | Update README.md: NORMALIZE is retryable |
| D-006 | Doc drift | maker8-specs.md | Specs describe `video.publish.result.v1` topic and Publisher Worker | Publisher Worker is not implemented; topic does not exist | Specs describe non-existent system | **Low** | Add "FUTURE — NOT YET IMPLEMENTED" label to Publisher sections |
| D-007 | Doc drift | OutputConfig.codec default | `README.md` says default codec is `"libx264"` | `render_contracts/render_spec.py` has `codec: str = "auto"` | Mismatch between docs and code default | **Medium** | Update README: default is `"auto"` (auto-selects based on GPU) |
| D-008 | Doc drift | RenderResult fields | `maker8-specs.md` RenderResult does not include `dry_run`, `canvas_profile`, `uploader_metadata`, `warnings`, `asset_report` | `contracts.py:RenderResult` includes all these fields | Spec is outdated; downstream consumers may not expect these fields | **Medium** | maker8-specs.md needs update or archive |
| D-009 | Doc drift | Architecture review §7.2 | Review says "invalid payload path chỉ log rồi return, không có DLQ" | `orchestrator.py:_send_invalid_payload_dlq()` exists and does send DLQ | Already fixed; review doc is now stale on this point | **Low** | Archive or annotate as resolved |
| D-010 | Doc drift | Architecture review §7.3 | Review says "EMIT_RESULT luôn phát ra configured topic" | Code actually uses `result_destination.topic` with fallback to config | Already fixed; review doc is now stale on this point | **Low** | Archive or annotate as resolved |

---

## 13. Gap Register

| ID | Type | Surface | Current state | Impact | Evidence | Severity | Decision |
|----|------|---------|---------------|--------|----------|----------|----------|
| G-001 | Test gap | Pipeline integration | No end-to-end pipeline tests with real FFmpeg/yt-dlp | Cannot prove runtime behavior; rely on manual testing | Only 5 test files; focus on models/helpers | **High** | Add integration test suite before go-live (at minimum: happy-path, degraded-path) |
| G-002 | Schema/example gap | JSON Schemas stale | Schemas do not include `dry_run`, `canvas_profile`, `publish_intent`, `uploader_metadata`, `UploaderMetadata`, `SourceAttribution` | External integrators get wrong schema | `grep` found zero matches | **High** | Regenerate all 3 JSON schemas from Pydantic models |
| G-003 | Schema/example gap | Examples stale | No example shows `dry_run`, `canvas_profile`, `uploader_metadata` | New integrators miss capabilities | Examples only have v1 original fields | **High** | Update examples to include new fields |
| G-004 | Observability gap | Health probe wiring | `WorkerState` and `HealthManager` exist but `flush_status()` mostly used in tests | Status JSON not fully leveraged for operational monitoring | Architecture review §7.6 | **Medium** | Wire `flush_status()` into runtime (already partially done via `set_on_change`) |
| G-005 | Operational gap | No consumer-driven contract test | No test validates that editor8's actual output parses correctly in maker8 | Schema drift could go undetected | Separate repos, no shared CI | **High** | Add cross-repo contract test or fixture-based validation |
| G-006 | Operational gap | No alerting/runbook | No runbook for DLQ processing, stuck jobs, credential expiry | Operator cannot respond to incidents systematically | No runbook files found | **High** | Create ops runbook before go-live |
| G-007 | Behavior gap | `publish_intent` orphaned | Field exists in contract but no stage reads it | Feature was added to schema but never implemented | Code search: zero reads | **Low** | Document as RESERVED or remove |
| G-008 | Operational gap | Secret rotation | TTS keys have rotation; Dropbox/Kafka do not | Credential expiry causes production outage with no mitigation | `config.py` shows flat secrets | **Medium** | Document rotation procedures; monitor expiry |
| G-009 | Behavior gap | `dry_run` not honored | `dry_run` is forwarded to result but no stage skips work (download, render, upload all execute) | Resource waste on dry_run; may produce unintended uploads | `orchestrator.py` does not check `dry_run` before running stages | **High** | Either implement dry_run behavior (skip DOWNLOAD→UPLOAD) or document as "label only" |
| G-010 | Test gap | Result/DLQ shape stability | No test verifies `RenderResult` and `DLQPayload` shapes match downstream expectations | Shape could regress without detection | `test_contracts.py` exists but is limited | **Medium** | Add snapshot tests for result/DLQ JSON shapes |
| G-011 | Operational gap | Kafka connect failure | No startup validation that Kafka is reachable | Worker starts but hangs silently on poll | `app.py` does not probe Kafka | **Medium** | Add Kafka connectivity check at startup |
| G-012 | Operational gap | Disk cleanup on failure | Cleanup is best-effort in `finally` block | Disk can fill with failed job artifacts | `orchestrator.py:_cleanup()` logs exception but doesn't fail | **Low** | Acceptable for go-live; add periodic cleanup as post-launch improvement |

---

## 14. Go-Live Blocker List

| ID | Blocker | Why critical | Owner | Action | From |
|----|---------|-------------|-------|--------|------|
| B-001 | JSON Schemas and examples are stale | External integrators get wrong contract; causes integration failures | Developer | Regenerate schemas; update examples | D-003, G-002, G-003 |
| B-002 | `CONTRACT_FIELD_STATUS.md` has wrong statuses | `result.topic`, `result.key` marked RESERVED but are ACTIVE; `AssetSourceOptions.format` marked RESERVED but is ACTIVE | Developer | Update field statuses | D-002, D-004 |
| B-003 | `dry_run` behavior undefined | Field is forwarded but not honored — upload and render still execute | PO + Developer | Decision: implement proper dry_run OR explicitly document as "label only, no skip" | G-009 |
| B-004 | No operational runbook | Cannot handle DLQ, stuck jobs, credential issues systematically | Developer + Ops | Create basic runbook | G-006 |
| B-005 | No cross-repo contract test | editor8 ↔ maker8 schema drift undetectable | Developer | Add fixture-based contract test | G-005 |

---

## 15. Production Readiness Matrix

| Dimension | Current state | Evidence | Risk | Severity | Decision |
|-----------|---------------|----------|------|----------|----------|
| **Contract stability** | render_contracts identical in both repos; schemas stale | `diff` verified; grep found no new fields in schemas | Integration drift from stale schemas | High | Fix schemas before go-live (B-001) |
| **Runtime behavior** | 8-stage pipeline functional; degradation model works | Code review + architecture review doc | Low runtime risk | Low | Acceptable |
| **Failure handling** | Stage-level retry, DLQ, degradation warnings | `retry.py`, `orchestrator.py` | Retry sleep blocks instance | Medium | Acceptable for initial scale |
| **Observability** | Structured logs, optional Prometheus metrics, health files | `observability/` package | Logs are primary diagnostic; metrics optional | Medium | Go-live with logs; wire metrics post-launch |
| **Editorial quality** | No automated quality gate | No content validation | Bad TTS or asset mismatch goes to output | High | PO decision: manual review pre-publish OR accept risk |
| **Source attribution** | `UploaderMetadata.source_attributions` forwarded | `render_spec.py:SourceAttribution` | Attribution depends on editor8 populating it | Medium | Document editor8 as owner of attribution data |
| **Copyright/compliance** | No automated check | maker8 downloads whatever editor8 specifies | Copyright risk is upstream | Medium | Accept: editor8/operator owns content rights |
| **Security** | Secrets from env; per-job dir mode 0o700; job_id validation prevents path traversal | `context.py`, `config.py` | No secret rotation for Dropbox/Kafka | Medium | Document rotation procedures (G-008) |
| **Capacity** | 1 instance = 1 job; typical job 5-30 min | Architecture review | Low throughput for high volume | Medium | Scale by adding instances; acceptable for initial go-live |
| **Deployment** | Docker + Docker Compose; CI/CD via GitHub Actions | `Dockerfile`, deployment/ | Health check alignment needed | Medium | Verify healthcheck paths match runtime |
| **Testing** | 5 test files, ~1200 lines; focused on contracts/helpers | `tests/` listing | No integration tests | High | Add minimum integration tests (G-001) |
| **Documentation** | Multiple scattered docs; inconsistencies found | This investigation | Operator confusion | High | This document is the fix |

---

## 16. Editorial, Attribution, Compliance Constraints

### Attribution chain

```
editor8 (populates UploaderMetadata.source_attributions)
  → maker8 (forwards to RenderResult.uploader_metadata)
    → downstream consumer (uses for publishing metadata)
```

maker8's role is **pass-through** for attribution. It does not validate or enrich attribution data.

### Editorial safeguards

- **No content validation in maker8**: narration text, asset URLs, and metadata are taken at face value
- **Degraded output risk**: PARTIAL status videos may have missing scenes, silent narration, or placeholder visuals
- **Recommendation**: PO should define whether PARTIAL videos are publishable or require manual review

### Compliance notes

- Downloaded assets may have copyright restrictions — editor8/operator is responsible for ensuring usage rights
- maker8 preserves `source_url`, `creator`, `license`, `credit_text` from `SourceAttribution` for audit trail
- No automated DMCA or platform policy check exists in the pipeline

---

## 17. Known Limitations & Reserved Fields

### Reserved fields (defined in schema, not consumed by maker8)

1. `Canvas.safe_area` — future: constrain layer placement
2. `SceneTiming.duration_mode` — future: support fixed/manual duration modes
3. `Layer.align` — future: alignment within rect (currently handled by anchor/rect)
4. `Transition.type` — future: support fade, wipe, etc. (currently always crossfade)
5. `PublishTarget.metadata` — future: platform-specific metadata
6. `PublishTarget.params` — future: platform-specific upload params
7. `ResultDestination.type` — always "kafka"; hardcoded
8. `PublishTarget.variant` — not consumed
9. `PublishTarget.enabled` — not consumed
10. `RenderRequest.publish_intent` — not consumed

### Architectural limitations

1. **Synchronous pipeline**: 1 job per instance; throughput limited to parallelism of instances
2. **Disk-based artifacts**: intermediate state on filesystem; cleanup failure can cause disk exhaustion
3. **MoviePy-first rendering**: CPU-bound scene composition even with GPU encoding
4. **Producer flush per message**: higher latency than batched production
5. **os._exit(0) shutdown**: bypasses Python cleanup to avoid C extension segfaults
6. **No Publisher Worker**: `publish.targets[]` is forwarded to result but actual publishing is not implemented

---

## 18. Decision Log

| # | Decision | Made by | Date | Rationale |
|---|----------|---------|------|-----------|
| 1 | `render_contracts` is the single source of truth for wire-format models | Architecture | Pre-investigation | Prevents duplication between editor8 and maker8 |
| 2 | Consumer always commits after handler returns | Developer | Pre-investigation | Orchestrator handles DLQ/result internally; double-processing is worse than message loss for failed jobs |
| 3 | `result.topic` and `result.key` are ACTIVE (used for routing) | Investigation | 2026-04-05 | Code evidence shows active use since emit.py was updated |
| 4 | NORMALIZE is retryable | Developer | Pre-investigation | Added to `RENDER_RETRYABLE_STAGES` to handle transient FFmpeg failures |
| 5 | `AssetSourceOptions.format` is ACTIVE | Investigation | 2026-04-05 | YouTube connector passes it to yt-dlp |
| 6 | `dry_run` semantics needs PO decision | Investigation | 2026-04-05 | Currently forwarded but not honored — go-live blocker B-003 |

---

## 19. Document Disposition

| Document | Disposition | Rationale |
|----------|-------------|-----------|
| `README.md` | **Keep as reference** | Quick start, overview; update retryability table and codec defaults |
| `docs/maker8-specs.md` | **Archive** | Original spec v1; largely superseded by this document; Publisher Worker sections not implemented |
| `docs/MAKER8_SYSTEM_ARCHITECTURE_AND_REVIEW.md` | **Archive** | Valuable analysis but several findings now resolved (§7.2, §7.3); this document captures remaining issues |
| `docs/IMPLEMENTATION_STATUS.md` | **Archive** | Snapshot from 2026-02-13; feature-complete claim still valid but details outdated |
| `CONTRACT_FIELD_STATUS.md` | **Merge into this document** | Field status matrix now lives in §8 of this document; update original or remove |
| `docs/KAFKA_INTEGRATION.md` | **Keep as reference** | Kafka setup guide; still accurate |
| `docs/TTS_CREDENTIAL_ROTATION.md` | **Keep as reference** | TTS key rotation guide; still accurate |
| `docs/SEGFAULT_TROUBLESHOOTING.md` | **Keep as reference** | Still relevant for `os._exit()` rationale |
| `docs/QUICK_REFERENCE.md` | **Keep as reference** | Quick reference card |
| `docs/schemas/*.json` | **Regenerate** | Currently stale; must be regenerated from Pydantic models |
| `docs/examples/*.json` | **Update** | Must include new fields |
| All request cards / incident docs | **Archive** | Historical tracking only; not source of truth |

---

## 20. Go-Live Conclusion

### Status: **CONDITIONAL**

The system can go live **if** the following blockers are resolved:

#### Must-fix before go-live

| # | Item | Owner | Action |
|---|------|-------|--------|
| B-001 | Regenerate JSON schemas and update examples with new fields | Developer | Run schema generation script; update 5 example files |
| B-002 | Update `CONTRACT_FIELD_STATUS.md` with correct ACTIVE/RESERVED statuses | Developer | Update `result.topic`, `result.key`, `AssetSourceOptions.format` |
| B-003 | PO decision on `dry_run` semantics | PO | Either implement skip behavior OR document as "label only" |
| B-004 | Create basic operational runbook | Developer + Ops | DLQ handling, credential issues, stuck jobs |
| B-005 | Add cross-repo contract fixture test | Developer | Ensure editor8 output parses in maker8 |

#### High-risk items accepted for go-live

| # | Item | Mitigation |
|---|------|------------|
| G-001 | No integration tests with real FFmpeg | Manual smoke testing; add tests post-launch |
| G-004 | Health probe wiring incomplete | Logs provide sufficient diagnostics initially |
| Editorial quality | No automated content quality gate | Manual review by PO before publishing |
| Copyright | No automated rights check | Editor8/operator responsible for asset rights |

#### Post-go-live backlog

| # | Item |
|---|------|
| G-008 | Document secret rotation for Dropbox/Kafka |
| G-011 | Add Kafka connectivity check at startup |
| G-012 | Periodic disk cleanup for failed job artifacts |
| D-005 | Update README retryability table |
| D-006 | Label Publisher Worker sections in specs as "FUTURE" |
| D-007 | Update README codec default to "auto" |

### Sign-off required

- **PO**: Output meets publishing needs; PARTIAL video policy defined; `dry_run` decision made
- **Architecture**: Contract is stable; deployment topology verified; health semantics aligned
- **Developer**: Blockers B-001–B-005 resolved; smoke test passed; runbook available
