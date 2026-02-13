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
src/maker8/
├── models/          # Pydantic v2 models
│   ├── common.py    # Shared enums, ErrorInfo, DropboxFileRef, OutputMeta, etc.
│   ├── spec.py      # RenderSpec: Canvas, Defaults, Asset, Scene, Layer, etc.
│   ├── contracts.py # RenderRequest, RenderResult, DLQPayload
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

### Models – DRY Rules

- **All shared types** live in `models/common.py` – never duplicate.
- Import `PublishTarget`, `ErrorInfo`, `DropboxFileRef`, etc. from `common`.
- Use `Field(alias=...)` for Python keyword conflicts (`in` → `in_`, `bytes` → `bytes_`).
- Use `model_config = {"populate_by_name": True}` when aliases exist.
- Serialize with `model_dump(mode="json", by_alias=True)` for Kafka / Dropbox output.

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
