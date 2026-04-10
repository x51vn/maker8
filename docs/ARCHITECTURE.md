# Maker8 – System Architecture

> **Canonical architecture document.**  
> Last updated: 2026-04-08 (XST-1038).

---

## 1. Overview

Maker8 is a **video rendering pipeline** (Render Worker) that consumes
`RenderRequest` messages from Kafka, executes an 8-stage pipeline to
produce an `.mp4` video, uploads artifacts to Dropbox, and emits a
`RenderResult` back to Kafka.

**Key characteristics:**

- **1 instance = 1 job at a time** (synchronous pipeline).
- Retry with exponential back-off for retryable stages.
- DLQ for exhausted retries or non-retryable failures.
- Prometheus metrics + health probes for observability.
- Plugin system for source connectors and effects.

---

## 2. System Block Diagram

```mermaid
block-beta
    columns 5

    %% External inputs / outputs
    kafka_in["Kafka\nvideo.render.request.v1"]:1
    space:3
    kafka_out["Kafka\nvideo.render.result.v1"]:1

    %% Core pipeline
    space:1
    pipeline["Pipeline Orchestrator\n(8 stages)"]:3
    space:1

    %% External services row
    ytdlp["yt-dlp\n(YouTube/HTTP)"]:1
    ffmpeg["FFmpeg/ffprobe\n(normalize/encode)"]:1
    tts["TTS Providers\n(gTTS / GCP / ElevenLabs)"]:1
    dropbox["Dropbox API\n(upload)"]:1
    kafka_dlq["Kafka\nvideo.render.dlq.v1"]:1

    %% Platform layer
    plugins["Plugin Registry\n(source connectors + effects)"]:2
    health["Health Probes\n+ Prometheus Metrics"]:2
    config["Config\n(env vars)"]:1

    kafka_in --> pipeline
    pipeline --> kafka_out
    pipeline --> kafka_dlq
    pipeline --> ytdlp
    pipeline --> ffmpeg
    pipeline --> tts
    pipeline --> dropbox
```

---

## 3. Pipeline Stages

The pipeline executes 8 stages in strict order. Each stage operates on a
shared `PipelineContext` that carries mutable state between stages.

```mermaid
flowchart LR
    V[VALIDATE] --> RA[RESOLVE_ASSETS]
    RA --> DL[DOWNLOAD]
    DL --> N[NORMALIZE]
    N --> T[TTS]
    T --> R[RENDER]
    R --> UD[UPLOAD_DROPBOX]
    UD --> ER[EMIT_RESULT]

    style V fill:#e8f5e9
    style RA fill:#fff9c4
    style DL fill:#fff9c4
    style N fill:#fff9c4
    style T fill:#fff9c4
    style R fill:#e8f5e9
    style UD fill:#fff9c4
    style ER fill:#fff9c4
```

| # | Stage | Retryable | Description |
|---|-------|-----------|-------------|
| 1 | `VALIDATE` | No | Validate RenderSpec schema, ≥1 scene, asset refs |
| 2 | `RESOLVE_ASSETS` | Yes | Query source plugins for download plans |
| 3 | `DOWNLOAD` | Yes | Execute plugin downloads to local disk |
| 4 | `NORMALIZE` | Yes | FFmpeg normalize/transcode, NVENC with fallback |
| 5 | `TTS` | Yes | Synthesize narration per scene |
| 6 | `RENDER` | No | MoviePy/Pillow video composition |
| 7 | `UPLOAD_DROPBOX` | Yes | Upload .mp4 + manifest to Dropbox |
| 8 | `EMIT_RESULT` | Yes | Publish RenderResult to Kafka |

**Dry-run mode:** When `dry_run=true`, stages 2–7 are skipped. Only
VALIDATE and EMIT_RESULT execute.

---

## 4. Data Flow Diagram

```mermaid
flowchart TB
    subgraph External
        KIn[Kafka Consumer\nvideo.render.request.v1]
        KOut[Kafka Producer\nvideo.render.result.v1]
        KDLQ[Kafka Producer\nvideo.render.dlq.v1]
        DBX[Dropbox]
        TTS_SVC[TTS Providers]
        YTDLP[yt-dlp / HTTP]
    end

    subgraph Orchestrator
        O[Orchestrator]
        CTX[PipelineContext]
    end

    subgraph Stages
        S1[ValidateStage]
        S2[ResolveAssetsStage]
        S3[DownloadStage]
        S4[NormalizeStage]
        S5[TTSStage]
        S6[RenderStageImpl]
        S7[UploadDropboxStage]
        S8[EmitResultStage]
    end

    subgraph Artifacts ["Local Artifacts ($MAKER8_WORK_DIR/{job_id})"]
        A_DIR[assets/]
        T_DIR[tts/]
        O_DIR[output/]
    end

    KIn -->|RenderRequest JSON| O
    O --> CTX
    CTX --> S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7 --> S8

    S2 -->|resolve plans| YTDLP
    S3 -->|download files| A_DIR
    S4 -->|ffmpeg normalize| A_DIR
    S5 -->|synthesize audio| TTS_SVC
    S5 -->|.mp3 files| T_DIR
    S6 -->|compose .mp4| O_DIR
    S7 -->|upload .mp4 + manifest| DBX
    S8 -->|RenderResult| KOut

    O -->|on failure| KDLQ
```

---

## 5. Job Lifecycle Sequence

```mermaid
sequenceDiagram
    participant K as Kafka Consumer
    participant O as Orchestrator
    participant V as ValidateStage
    participant RA as ResolveAssetsStage
    participant DL as DownloadStage
    participant N as NormalizeStage
    participant T as TTSStage
    participant R as RenderStageImpl
    participant U as UploadDropboxStage
    participant E as EmitResultStage
    participant KP as Kafka Producer

    K->>O: RenderRequest
    O->>V: execute(ctx)
    V-->>O: validated
    O->>RA: execute(ctx)
    RA-->>O: resolved plans
    O->>DL: execute(ctx)
    DL-->>O: assets on disk
    O->>N: execute(ctx)
    N-->>O: normalized proxies
    O->>T: execute(ctx)
    T-->>O: TTS audio files
    O->>R: execute(ctx)
    R-->>O: .mp4 composed
    O->>U: execute(ctx)
    U-->>O: Dropbox URLs
    O->>E: execute(ctx)
    E->>KP: RenderResult

    Note over O: On retryable failure:<br/>retry with backoff<br/>(max 5 attempts)
    Note over O: On permanent failure:<br/>emit DLQ message
```

---

## 6. Retry Policy

| Parameter | Value |
|-----------|-------|
| Max attempts | 5 |
| Min delay | 60 seconds |
| Max delay | 21,600 seconds (6 hours) |
| Jitter factor | 0.1 |
| Back-off formula | `min(60 × 2^(attempt-1), 21600) + jitter` |

**Retryable stages:** RESOLVE_ASSETS, DOWNLOAD, NORMALIZE, TTS,
UPLOAD_DROPBOX, EMIT_RESULT.

**Non-retryable stages:** VALIDATE, RENDER. Failures here send directly
to DLQ.

---

## 7. Kafka Topics

| Topic | Direction | Schema |
|-------|-----------|--------|
| `video.render.request.v1` | **Inbound** | `RenderRequest` (render_contracts) |
| `video.render.result.v1` | **Outbound** | `RenderResult` (models/contracts.py) |
| `video.render.dlq.v1` | **DLQ** | `DLQPayload` (models/contracts.py) |

- Consumer group: `maker8-render`
- Max poll interval: 30 minutes (1,800,000 ms)
- SASL_PLAINTEXT authentication

---

## 8. External Services

| Service | Client | Purpose |
|---------|--------|---------|
| Dropbox | `DropboxClient` | Upload .mp4 + manifest; OAuth2 refresh |
| Google Cloud TTS | `GoogleCloudTTSProvider` | Neural TTS; service-account key rotation |
| ElevenLabs TTS | `ElevenLabsProvider` | Neural TTS; API key rotation |
| gTTS | `GTTSProvider` | Free Google Translate TTS (fallback) |
| yt-dlp | `YouTubeSourceConnector` | Download YouTube videos |
| HTTP | `HttpSourceConnector` | Download direct HTTP URLs |
| FFmpeg/ffprobe | subprocess | Normalize, transcode, probe metadata |

---

## 9. Plugin System

```mermaid
classDiagram
    class SourceConnectorPlugin {
        <<abstract>>
        +manifest() PluginManifest
        +schema() dict
        +resolve(asset_id, source) ResolvedAssetPlan
        +download(plan, dest_dir) Path
    }
    class EffectPlugin {
        <<abstract>>
        +manifest() PluginManifest
        +schema() dict
        +apply(ctx, ir, instance) Any
        +has_ffmpeg_filter() bool
        +ffmpeg_filter_graph() str
    }
    class PluginRegistry {
        +register_source(kind, plugin)
        +register_effect(kind, plugin)
        +get_source(kind) SourceConnectorPlugin
        +get_effect(kind) EffectPlugin
        +load_defaults()
    }

    SourceConnectorPlugin <|-- YouTubeSourceConnector
    SourceConnectorPlugin <|-- HttpSourceConnector
    PluginRegistry --> SourceConnectorPlugin
    PluginRegistry --> EffectPlugin
```

**Built-in source connectors:** `youtube`, `http`

**Built-in effect plugins:** fade, zoom_pan, blur, brightness_contrast,
and others registered in `PluginRegistry.load_defaults()`.

---

## 10. Health & Observability

### Health Probes

| Probe | File | Meaning |
|-------|------|---------|
| Liveness | `/tmp/maker8_live` | Process is alive |
| Readiness | `/tmp/maker8_ready` | Bootstrap complete, consuming |
| Status | `/tmp/maker8_status.json` | JSON snapshot of worker state |

### Prometheus Metrics (port 9108)

**Counters:**
- `maker8_jobs_received_total` — Kafka messages received
- `maker8_jobs_succeeded_total` — Jobs completed successfully
- `maker8_jobs_failed_total{stage, error_code}` — Permanent failures
- `maker8_retries_scheduled_total{stage}` — Retries triggered
- `maker8_dlq_emitted_total{stage}` — DLQ messages sent
- `maker8_subprocess_failures_total{stage, source_kind}` — Process failures

**Histograms:**
- `maker8_job_duration_seconds{status}` — End-to-end job time
- `maker8_stage_duration_seconds{stage, status}` — Per-stage time
- `maker8_tts_duration_seconds{provider}` — TTS synthesis time
- `maker8_download_bytes{source_kind}` — Downloaded asset sizes
- `maker8_scene_render_duration_seconds` — Per-scene render time

**Gauges:**
- `maker8_worker_up` — Process alive (0/1)
- `maker8_worker_ready` — Consuming (0/1)
- `maker8_job_in_progress` — Processing a job (0/1)
- `maker8_current_stage` — Ordinal 0–8 (0 = idle)

---

## 11. Artifact Layout

Each job creates a workspace under `$MAKER8_WORK_DIR/{job_id}/`:

```
{job_id}/
├── assets/          # Downloaded & normalized media files
├── tts/             # Synthesized narration audio (.mp3)
└── output/          # Final composed video (.mp4)
```

---

## 12. Configuration

All settings use the `MAKER8_` environment variable prefix.

| Category | Key Variables |
|----------|--------------|
| Kafka | `MAKER8_KAFKA_BOOTSTRAP_SERVERS`, `MAKER8_KAFKA_GROUP_ID` |
| Dropbox | `MAKER8_DROPBOX_APP_KEY`, `MAKER8_DROPBOX_APP_SECRET`, `MAKER8_DROPBOX_REFRESH_TOKEN` |
| TTS | `MAKER8_TTS_PROVIDER`, `MAKER8_TTS_PRESETS_PATH`, `MAKER8_GG_TTS_KEYS_DIR`, `MAKER8_ELEVENLABS_KEYS_DIR` |
| Rendering | `MAKER8_USE_NVENC`, `MAKER8_PERF_PROFILE`, `MAKER8_PROXY_MAX_SHORT_EDGE` |
| yt-dlp | `MAKER8_YTDLP_AUTO_UPDATE`, `MAKER8_YTDLP_CHANNEL` |
| Metrics | `MAKER8_METRICS_ENABLED`, `MAKER8_METRICS_PORT` |
| Retry | `MAKER8_RETRY_MAX_ATTEMPTS`, `MAKER8_RETRY_MIN_DELAY_SEC` |
| Work dir | `MAKER8_WORK_DIR` |

---

## 13. Source-of-Truth Hierarchy

| File | Authority Scope |
|------|-----------------|
| `render_contracts/render_spec.py` | Wire-format models (shared with editor8) |
| `render_contracts/events.py` | Kafka topic constants |
| `models/common.py` | maker8-internal enums, ErrorInfo, DropboxFileRef |
| `models/contracts.py` | RenderRequest (re-export), RenderResult, DLQPayload |
| `config.py` | All runtime configuration |
| `retry.py` | Retry policy, StageError, retryable stage set |
| `pipeline/orchestrator.py` | Stage ordering and execution flow |
| `plugins/registry.py` | Default plugin registration |
| `config/tts_presets.json` | TTS voice/preset definitions |
| `CONTRACT_FIELD_STATUS.md` | Field lifecycle status tracking |
| `docs/ARCHITECTURE.md` | **This document** — canonical architecture reference |
