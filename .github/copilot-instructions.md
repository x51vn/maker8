# Maker8 – Copilot Instructions

## Project Overview

Maker8 is a **video rendering pipeline** (Render Worker) that:

1. Consumes job JSON from Kafka (`video.render.request.v1`)
2. Validates & canonicalizes the `RenderSpec`
3. Resolves & downloads media assets (yt-dlp, HTTP)
4. Generates TTS narration per scene
5. Composes scene-based video via MoviePy / FFmpeg
6. Uploads `.mp4` + manifest to Dropbox
7. Emits `video.render.result.v1` to Kafka

Publisher Worker is **not yet implemented** – only the render pipeline is in scope.

---

## Architecture

```
Kafka ──► RenderConsumer ──► Orchestrator ──► [Stage pipeline] ──► KafkaProducer
                                                 │
                 ┌───────────────────────────────┘
                 ▼
  VALIDATE → RESOLVE_ASSETS → DOWNLOAD → NORMALIZE → TTS → RENDER → UPLOAD_DROPBOX → EMIT_RESULT
```

- **1 instance = 1 job at a time** (synchronous pipeline)
- Retry with exponential backoff for retryable stages
- DLQ for exhausted retries or non-retryable failures

---

## Project Structure

```
src/
├── render_contracts/  # Canonical wire-format models (shared with editor8)
│   ├── __init__.py
│   ├── render_spec.py  # 25+ Pydantic models: Canvas, Scene, Layer, RenderSpec, etc.
│   └── events.py      # Kafka topic constants
└── maker8/
    ├── models/          # Pydantic v2 models
    │   ├── common.py    # Shared enums, ErrorInfo, DropboxFileRef + re-exports from render_contracts
    │   ├── spec.py      # Re-exports all wire-format types from render_contracts
    │   ├── contracts.py # RenderRequest (re-export), RenderResult, DLQPayload
    │   └── manifest.py  # Dropbox manifest
    ├── kafka/           # Kafka consumer / producer wrappers
    ├── pipeline/        # Pipeline stages (one file per stage)
    │   ├── context.py   # PipelineContext (mutable state passed between stages)
    │   ├── stage.py     # Stage ABC
    │   └── orchestrator.py
    ├── plugins/         # Extensible plugin system
    │   ├── base.py      # ABCs: SourceConnectorPlugin, EffectPlugin
    │   ├── registry.py  # Singleton plugin registry
    │   └── sources/     # Built-in source connectors (youtube, http)
    ├── rendering/       # MoviePy / Pillow video composition
    │   ├── composer.py  # Scenes → final video
    │   ├── layers.py    # Layer → MoviePy clip
    │   └── text.py      # Text rendering via Pillow
    ├── services/        # External service clients
    │   ├── dropbox_client.py
    │   └── tts_client.py
    ├── utils/           # Hashing, logging
    ├── canon.py         # Canonicalization + job_key
    ├── retry.py         # RetryPolicy, StageError, backoff
    ├── config.py        # Pydantic Settings (env vars)
    └── app.py           # Entry point
```

---

## Coding Conventions

### General

- **Python ≥ 3.11** – use modern type syntax (`X | None`, `list[T]`).
- Every file starts with `from __future__ import annotations`.
- Use `structlog` for all logging (via `utils/logging.py`).
- Prefer explicit imports; avoid wildcard imports.
- **All imports must be at the top of the file** – never put `import` or `from … import` statements inside functions, methods, or class bodies.
  - Exception: circular-import guards that cannot be resolved otherwise (very rare). In that case add a `# noqa: PLC0415` comment to acknowledge the deliberate exception.
  - For optional/heavy dependencies that may not be installed, use a module-level `try/except ImportError` block at the top, **not** a lazy import inside a function.
- **`mypy --strict`** is enforced in CI. `no_implicit_reexport` is active.
  - Any module that re-exports a symbol from another package **must** declare an explicit `__all__` containing every re-exported name.
  - Do **not** suppress `attr-defined` or `no_implicit_reexport` errors with blanket ignores. Fix the export surface instead.
  - Remove stale `# type: ignore[…]` comments when the underlying issue is resolved — unused ignores fail CI.

### Shared Wire-Format Contracts (`render_contracts`)

- **`render_contracts/render_spec.py`** is the **single source of truth** for all wire-format Pydantic models (`Canvas`, `Scene`, `Layer`, `RenderSpec`, `RenderRequest`, `Trace`, `PublishTarget`, etc.).
- **Never duplicate** these model definitions in `maker8`. Import from `render_contracts` instead.
- `maker8.models.spec` re-exports all 25 types from `render_contracts.render_spec`.
- `maker8.models.common` re-exports `PublishTarget` and `Trace`.
- `maker8.models.contracts` re-exports `RenderRequest` and `ResultDestination`.
- When adding a field to the wire format, edit `render_contracts/render_spec.py` and update the same file in editor8. Both projects must stay in sync.
- Re-export modules **must** include the re-exported names in their `__all__` list (required by `mypy --strict` / `no_implicit_reexport`).

### Models – DRY Rules

- **Wire-format types** are defined in `render_contracts/render_spec.py` and re-exported via `models/spec.py`, `models/common.py`, `models/contracts.py`.
- **maker8-internal types** (enums, `ErrorInfo`, `DropboxFileRef`, `OutputMeta`, etc.) live in `models/common.py` – never duplicate.
- Import `PublishTarget`, `Trace` from `models/common.py`; import `RenderRequest`, `ResultDestination` from `models/contracts.py`.
- Use `Field(alias=...)` for Python keyword conflicts (`in` → `in_`, `bytes` → `bytes_`).
- Use `model_config = {"populate_by_name": True}` when aliases exist.
- Serialize with `model_dump(mode="json", by_alias=True)` for Kafka / Dropbox output.
- **Every re-export module must have `__all__`** listing all public names (for `mypy --strict`).

### Pipeline Stages

- Each stage is a class inheriting `Stage` (from `pipeline/stage.py`).
- Stage `name` maps to `RenderStage` enum.
- Stages communicate **only** through `PipelineContext`.
- On failure, raise `StageError(stage, code, message, retryable)`.
- Retryable stages: `RESOLVE_ASSETS, DOWNLOAD, TTS, UPLOAD_DROPBOX, EMIT_RESULT`.
- Non-retryable: `VALIDATE, NORMALIZE, RENDER`.

### Plugins

- Source connectors: `resolve(asset_id, source) → ResolvedAssetPlan`,
  `download(plan, dest_dir) → Path`.
- Effects: `apply(ctx, ir, instance) → ir`.
- Register in `PluginRegistry.load_defaults()`.

### Rendering

- `rendering/` **must not** import from `pipeline/` – it receives a `RenderInput` dataclass.
- `pipeline/render.py` bridges context → `RenderInput` → `compose_video()`.

### Error Handling

- `StageError`: pipeline errors (raised in stages, caught by orchestrator).
- `ErrorInfo`: serialized error in Kafka messages (models/common.py).
- Orchestrator wraps retryable stages with `RetryPolicy`.

---

## Adding a New Pipeline Stage

1. Add enum value to `RenderStage` in `models/common.py`.
2. Create `pipeline/<stage_name>.py` with a class extending `Stage`.
3. Register in `pipeline/orchestrator.py` stage list.
4. Add retry config in `retry.py` if retryable.

## Adding a New Source Connector

1. Create `plugins/sources/<kind>.py` implementing `SourceConnectorPlugin`.
2. Register in `PluginRegistry.load_defaults()`.

## Adding a New TTS Provider

1. Subclass `TTSProvider` in `services/tts_client.py`.
2. Register in `TTSService._PROVIDERS`.
3. Add preset entries to `config/tts_presets.json`.
