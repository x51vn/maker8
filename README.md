# Maker8 – Video Render Pipeline

Scene-based video rendering pipeline that consumes Kafka messages, ingests media,
generates TTS narration, composes video via MoviePy/FFmpeg, and uploads results to Dropbox.

## Table of Contents

- [Architecture](#architecture)
- [Pipeline Stages](#pipeline-stages)
- [Kafka Contracts](#kafka-contracts)
- [RenderSpec Reference](#renderspec-reference)
- [Configuration](#configuration)
- [TTS Providers & Presets](#tts-providers--presets)
- [Effect Plugins](#effect-plugins)
- [Source Connectors](#source-connectors)
- [Deployment](#deployment)
- [Extending](#extending)
- [Project Structure](#project-structure)

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
- Retry with exponential backoff (1 min → 6 h, up to 5 attempts) for retryable stages
- Dead-letter queue (DLQ) for exhausted retries or non-retryable failures
- Each job gets an isolated work directory (`/tmp/maker8/<job_id>/`) cleaned up after completion

---

## Pipeline Stages

| # | Stage | Description | Retryable |
|---|-------|-------------|-----------|
| 1 | `VALIDATE` | Validate spec structure, enforce rules, compute `job_key` | No |
| 2 | `RESOLVE_ASSETS` | Map each asset to a download plan via source connectors | Yes |
| 3 | `DOWNLOAD` | Download all resolved assets to local disk | Yes |
| 4 | `NORMALIZE` | Normalize media files (codec, resolution) via FFmpeg | Yes |
| 5 | `TTS` | Synthesize narration audio for each scene | Yes |
| 6 | `RENDER` | Compose scenes into final video via MoviePy/FFmpeg | No |
| 7 | `UPLOAD_DROPBOX` | Upload `.mp4` + `.manifest.json` to Dropbox | Yes |
| 8 | `EMIT_RESULT` | Produce `RenderResult` to Kafka result topic | Yes |

### Validation Rules

The `VALIDATE` stage enforces:

- `spec_version` must be `"1.0"` or `"2.0"`
- Canvas `w` > 0, `h` > 0, `fps` > 0
- At least one scene must exist
- All `scene_id` values must be unique
- All `asset.id` values must be unique
- Every scene must have non-empty `narration.text`
- All `asset_ref` values in layers and audio tracks must reference a declared asset

#### V2-specific validation (`spec_version: "2.0"`)

- `planning.planned_scene_count` (if present) must match actual scene count
- Layer `role` must be one of: `primary_visual`, `supporting_visual`, `title`, `logo`, `cta`, `decorative_text`
- Layer `missing_asset_policy` must be one of: `drop_layer`, `skip_scene`, `scene_placeholder`, `fail_request`
- Scene `subtitle.source` must be `"narration"` or `"custom"`
- `subtitle.source="custom"` requires non-empty `subtitle.text`

### Retry Policy

Retryable stages use exponential backoff with jitter:

- **Max attempts**: 5 (configurable via `MAKER8_RENDER_MAX_ATTEMPTS`)
- **Min delay**: 60 seconds
- **Max delay**: 21,600 seconds (6 hours)
- **Jitter**: ±10% of base delay

When all retries are exhausted, the orchestrator:
1. Emits a `FAILED` `RenderResult` to `video.render.result.v1`
2. Emits a `DLQPayload` to `video.render.dlq.v1`

---

## Kafka Contracts

### Topics

| Topic | Direction | Description |
|-------|-----------|-------------|
| `video.render.request.v1` | Inbound | Job submission |
| `video.render.result.v1` | Outbound | Render outcome (DONE / FAILED) |
| `video.render.dlq.v1` | Outbound | Dead-letter for failed jobs |

### Authentication

Supports SASL/PLAIN authentication:

```env
MAKER8_KAFKA_BOOTSTRAP_SERVERS=<kafka-host>:9094
MAKER8_KAFKA_SECURITY_PROTOCOL=SASL_PLAINTEXT
MAKER8_KAFKA_SASL_MECHANISM=PLAIN
MAKER8_KAFKA_USERNAME=client
MAKER8_KAFKA_PASSWORD=client-secret
```

### 1. RenderRequest (`video.render.request.v1`)

**Required fields**: `job_id`, `render_spec`

```json
{
  "job_id": "job-2025-01-15-abc123",
  "spec_version": "1.0",
  "render_spec": { "...see RenderSpec below..." },
  "result": {
    "type": "kafka",
    "topic": "video.render.result.v1",
    "key": ""
  },
  "trace": {
    "correlation_id": "corr-20250115-xyz789"
  }
}
```

**Full example**: [`docs/examples/render_request.example.json`](docs/examples/render_request.example.json)
**Minimal example**: [`docs/examples/render_request_minimal.example.json`](docs/examples/render_request_minimal.example.json)
**JSON Schema**: [`docs/schemas/render_request.schema.json`](docs/schemas/render_request.schema.json)

### 2. RenderResult (`video.render.result.v1`)

**Required fields**: `job_id`, `status`

```json
{
  "job_id": "job-2025-01-15-abc123",
  "status": "DONE",
  "job_key": "sha256:...",
  "dropbox": {
    "video": { "path": "/renders/2025/01/15/...", "file_id": "...", "rev": "...", "sha256": "...", "bytes": 52428800, "mime": "video/mp4" },
    "manifest": { "path": "...", "file_id": "...", "rev": "...", "bytes": 1024, "mime": "application/json" }
  },
  "output_meta": { "duration": 32.5, "w": 1080, "h": 1920, "fps": 30.0, "size_bytes": 52428800 },
  "publish_targets": [ { "platform": "youtube", "account_ref": "...", "metadata": {}, "params": {} } ],
  "asset_report": [ { "asset_id": "bg-video-1", "source_kind": "youtube", "filename": "bg-video-1.mp4", "size_bytes": 45000000 } ],
  "engine_versions": { "moviepy": "2.1.1", "ffmpeg": "6.1.1", "youtube_dlp": "2024.12.06" },
  "trace": { "correlation_id": "..." },
  "error": null
}
```

**Success example**: [`docs/examples/render_result_success.example.json`](docs/examples/render_result_success.example.json)
**Failed example**: [`docs/examples/render_result_failed.example.json`](docs/examples/render_result_failed.example.json)
**JSON Schema**: [`docs/schemas/render_result.schema.json`](docs/schemas/render_result.schema.json)

### 3. DLQPayload (`video.render.dlq.v1`)

**Required fields**: `job_id`, `failed_stage`, `attempts`

```json
{
  "job_id": "job-2025-01-15-abc123",
  "job_key": "sha256:...",
  "failed_stage": "DOWNLOAD",
  "attempts": 5,
  "last_error": { "code": "DOWNLOAD_FAILED", "stage": "DOWNLOAD", "retryable": true, "message": "HTTP 503 ..." },
  "dropbox": {},
  "trace": { "correlation_id": "..." }
}
```

**Example**: [`docs/examples/dlq_payload.example.json`](docs/examples/dlq_payload.example.json)
**JSON Schema**: [`docs/schemas/dlq_payload.schema.json`](docs/schemas/dlq_payload.schema.json)

---

## RenderSpec Reference

The `render_spec` is the core data structure that describes the video to render.

### Canvas

Defines the output video dimensions and frame rate.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `w` | int | 1080 | Width in pixels |
| `h` | int | 1920 | Height in pixels |
| `fps` | int | 30 | Frames per second |
| `bg` | string | `"#000000"` | Background fill color (hex) |
| `safe_area` | object | null | Insets: `{top, right, bottom, left}` |

### Defaults

Scene-level defaults inherited when the scene omits them.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `narration.lang` | string | `"vi-VN"` | Default narration language |
| `narration.tts_preset_ref` | string | `"tts:vi:default"` | Default TTS preset |
| `scene_timing.head_pad_sec` | float | 0.15 | Silence before narration |
| `scene_timing.tail_pad_sec` | float | 0.45 | Silence after narration |
| `scene_timing.duration_mode` | string | `"auto_from_tts"` | How scene duration is determined |

### Asset

Each asset declares an external media source to download.

```json
{
  "id": "bg-video-1",
  "type": "video",
  "source": {
    "kind": "youtube",
    "url": "https://www.youtube.com/watch?v=...",
    "options": {
      "format": "bestvideo[height<=1080]+bestaudio/best",
      "max_duration_sec": 300
    }
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique identifier (referenced by layers/audio tracks) |
| `type` | string | Yes | `"video"`, `"image"`, or `"audio"` |
| `source.kind` | string | Yes | Source connector: `"youtube"` or `"http"` |
| `source.url` | string | Yes | Download URL |
| `source.options.format` | string | No | yt-dlp format string (YouTube only) |
| `source.options.max_duration_sec` | int | No | Max video duration limit |

### Scene

Scenes are rendered sequentially. Each scene contains layers, audio tracks, effects, and narration.

```json
{
  "scene_id": "scene-001",
  "duration": null,
  "narration": { "text": "...", "lang": "vi-VN", "tts_preset_ref": "tts:vi:google_cloud" },
  "layers": [ "..." ],
  "audio_tracks": [ "..." ],
  "effects": [ "..." ],
  "transition_out": { "type": "crossfade", "duration": 0.5 }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `scene_id` | string | Yes | Unique scene identifier |
| `duration` | float | No | Explicit duration in seconds (null = auto from TTS) |
| `narration.text` | string | Yes | Text to synthesize |
| `narration.lang` | string | No | Override language |
| `narration.tts_preset_ref` | string | No | Override TTS preset |
| `layers` | array | No | Visual layers (rendered bottom-to-top) |
| `audio_tracks` | array | No | Background audio tracks |
| `effects` | array | No | Post-processing effects |
| `transition_out` | object | No | Transition to next scene |

### Layer

A visual layer within a scene. Type is `"image"`, `"video"`, or `"text"`.

| Field | Type | Description |
|-------|------|-------------|
| `layer_id` | string | Unique layer identifier |
| `type` | enum | `"image"`, `"video"`, `"text"` |
| `rect` | object | `{x, y, w, h}` position and size |
| `anchor` | string | Anchor point (default: `"top_left"`) |
| `opacity` | float | 0.0–1.0 (default: 1.0) |
| `rotation_deg` | float | Rotation in degrees |
| `scale` | float | Scale factor (default: 1.0) |
| `asset_ref` | string | Asset ID (for image/video layers) |
| `fit` | string | `"cover"` or `"contain"` |
| `trim` | object | `{"in": 0, "out": 15}` — seconds to trim |
| `text` | string | Display text (for text layers) |
| `text_align` | string | `"left"`, `"center"`, `"right"` |
| `valign` | string | `"top"`, `"center"`, `"bottom"` |
| `style` | object | Text style: font, size, color, stroke, etc. |

### AudioTrack

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `asset_ref` | string | Required | Asset ID for the audio file |
| `trim` | object | null | `{"in": 0, "out": 30}` — trim range |
| `volume` | float | 1.0 | Volume multiplier |
| `loop` | bool | false | Loop audio to fill scene duration |

### Output

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `codec` | string | `"auto"` | Video codec (auto-selects h264_nvenc or libx264) |
| `audio_codec` | string | `"aac"` | Audio codec |
| `bitrate` | string | `"4000k"` | Video bitrate |
| `audio_bitrate` | string | `"192k"` | Audio bitrate |
| `preset` | string | `"medium"` | FFmpeg encoding preset |
| `pix_fmt` | string | `"yuv420p"` | Pixel format |

---

## Configuration

All settings via environment variables (prefix: `MAKER8_`).

### Credential Source (Recommended)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAKER8_CREDENTIAL_SOURCE` | `db` | `db` (editor8 `service_keys`) or `env_file` (legacy) |
| `MAKER8_EDITOR8_DATABASE_URL` | | PostgreSQL URL of editor8 DB (required in `db` mode) |
| `MAKER8_CREDENTIAL_CACHE_TTL_SEC` | `60.0` | DB credential cache TTL in seconds |

### Kafka

| Variable | Default | Description |
|----------|---------|-------------|
| `MAKER8_KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Bootstrap server(s) |
| `MAKER8_KAFKA_GROUP_ID` | `maker8-render` | Consumer group ID |
| `MAKER8_KAFKA_RENDER_REQUEST_TOPIC` | `video.render.request.v1` | Input topic |
| `MAKER8_KAFKA_RENDER_RESULT_TOPIC` | `video.render.result.v1` | Result topic |
| `MAKER8_KAFKA_RENDER_DLQ_TOPIC` | `video.render.dlq.v1` | DLQ topic |
| `MAKER8_KAFKA_USERNAME` | | SASL username (legacy `env_file` mode) |
| `MAKER8_KAFKA_PASSWORD` | | SASL password (legacy `env_file` mode) |
| `MAKER8_KAFKA_SECURITY_PROTOCOL` | | `SASL_PLAINTEXT` or `SASL_SSL` |
| `MAKER8_KAFKA_SASL_MECHANISM` | | `PLAIN` |

### Dropbox

| Variable | Default | Description |
|----------|---------|-------------|
| `MAKER8_DROPBOX_APP_KEY` | | Dropbox OAuth app key (legacy `env_file` mode) |
| `MAKER8_DROPBOX_APP_SECRET` | | Dropbox OAuth app secret (legacy `env_file` mode) |
| `MAKER8_DROPBOX_REFRESH_TOKEN` | | Dropbox OAuth refresh token (legacy `env_file` mode) |

### TTS

| Variable | Default | Description |
|----------|---------|-------------|
| `MAKER8_TTS_PROVIDER` | `gtts` | Default TTS provider |
| `MAKER8_TTS_PRESETS_PATH` | `config/tts_presets.json` | TTS preset mappings file (`env_file` mode) |
| `MAKER8_TTS_TIMEOUT_SEC` | `120.0` | Max seconds per TTS call |
| `MAKER8_GOOGLE_TTS_KEYS_DIR` | `gg-tts-keys` | Directory with Google SA JSONs (legacy `env_file` mode) |
| `MAKER8_ELEVENLABS_API_KEY` | | Single ElevenLabs key fallback (legacy `env_file` mode) |
| `MAKER8_ELEVENLABS_KEYS_DIR` | `elevenlabs-keys` | Directory with ElevenLabs keys (legacy `env_file` mode) |

### Pipeline

| Variable | Default | Description |
|----------|---------|-------------|
| `MAKER8_WORK_DIR` | `/tmp/maker8` | Base work directory |
| `MAKER8_RENDER_MAX_ATTEMPTS` | `5` | Max retries for retryable stages |
| `MAKER8_RENDER_RETRY_MIN_DELAY_SEC` | `60.0` | Minimum backoff delay |
| `MAKER8_RENDER_RETRY_MAX_DELAY_SEC` | `21600.0` | Maximum backoff delay (6h) |
| `MAKER8_LOG_LEVEL` | `INFO` | Log level |
| `MAKER8_LOG_FORMAT` | `json` | `json` or `console` |

---

## TTS Providers & Presets

Three TTS backends are supported. The provider is selected per-scene via `tts_preset_ref`.

### Provider: `gtts` (default, free)

Uses Google Translate TTS. No API key needed. Limited voice quality.

### Provider: `google_cloud`

Uses Google Cloud Text-to-Speech API with Neural2 voices.
Supports round-robin key rotation: place multiple service-account JSON files in `gg-tts-keys/`.

### Provider: `elevenlabs`

Uses ElevenLabs API with multilingual voices.
Supports round-robin key rotation: place `.txt`/`.key` files in `elevenlabs-keys/`.

### Preset Configuration (`config/tts_presets.json`)

```json
{
  "tts:vi:default":       { "provider": "google_cloud", "lang": "vi-VN", "voice_name": "vi-VN-Neural2-A", "speaking_rate": 1.0, "pitch": 0.0, "audio_encoding": "MP3" },
  "tts:en:default":       { "provider": "google_cloud", "lang": "en-US", "voice_name": "en-US-Neural2-J", "speaking_rate": 1.0, "pitch": 0.0, "audio_encoding": "MP3" },
  "tts:vi:google_cloud":  { "provider": "google_cloud", "lang": "vi-VN", "voice_name": "vi-VN-Neural2-A", "speaking_rate": 1.0, "pitch": 0.0, "audio_encoding": "MP3" },
  "tts:en:google_cloud":  { "provider": "google_cloud", "lang": "en-US", "voice_name": "en-US-Neural2-J", "speaking_rate": 1.0, "pitch": 0.0, "audio_encoding": "MP3" },
  "tts:vi:elevenlabs":    { "provider": "elevenlabs", "lang": "vi", "voice_id": "...", "model_id": "eleven_multilingual_v2", "stability": 0.5, "similarity_boost": 0.75 },
  "tts:en:elevenlabs":    { "provider": "elevenlabs", "lang": "en", "voice_id": "...", "model_id": "eleven_multilingual_v2", "stability": 0.5, "similarity_boost": 0.75 }
}
```

---

## Effect Plugins

Effects are applied per-scene during the RENDER stage.

| Plugin ID | Description | Parameters |
|-----------|-------------|------------|
| `effect:fade` | Fade in/out | `fade_in`: float (sec), `fade_out`: float (sec) |
| `effect:zoom_pan` | Ken Burns zoom/pan | `start_zoom`: float, `end_zoom`: float, `direction`: string |
| `effect:blur` | Gaussian blur | `radius`: float |
| `effect:brightness_contrast` | Brightness/contrast adjust | `brightness`: float (multiplier), `contrast`: float (multiplier) |
| `effect:slide` | Slide in/out animation | `direction`: string (`"left"`, `"right"`, `"up"`, `"down"`) |
| `effect:color_overlay` | Color tint overlay | `color`: string (hex), `opacity`: float |
| `effect:grayscale` | Convert to grayscale | _(none)_ |
| `effect:rotate` | Rotate the scene | `angle`: float (degrees) |
| `effect:mirror` | Mirror/flip | `axis`: string (`"horizontal"`, `"vertical"`) |
| `effect:chroma_key` | Green screen removal | `color`: string (hex), `threshold`: float |

Usage in scene:

```json
{
  "effects": [
    { "plugin_id": "effect:zoom_pan", "params": { "start_zoom": 1.0, "end_zoom": 1.2 } },
    { "plugin_id": "effect:fade", "params": { "fade_in": 0.5, "fade_out": 0.3 } }
  ]
}
```

---

## Source Connectors

### `youtube`

Downloads video from YouTube (and other yt-dlp supported sites).

- Uses `yt-dlp` under the hood
- Supports format selection via `source.options.format`
- Default format: `bestvideo[height<=1920]+bestaudio/best`

### `http`

Direct HTTP/HTTPS file download.

- Auto-detects file type from URL extension
- Connect timeout: 30s, read timeout: 600s
- Download size limit: 2 GiB
- Stream download with 64 KiB chunks

---

## Deployment

### Docker (Production)

The worker is deployed as a Docker container via CI/CD (GitHub Actions → private registry `docker.x51.vn/x-ai/maker8`).

```yaml
# In docker-compose.yml
maker8:
  image: docker.x51.vn/x-ai/maker8:latest
  env_file: .env
  volumes:
    - ./gg-tts-keys:/app/gg-tts-keys:ro
    - ./elevenlabs-keys:/app/elevenlabs-keys:ro
  deploy:
    resources:
      limits:
        cpus: "2"
        memory: 4G
```

### Health Check

The worker uses file-based health probes:

- `/tmp/maker8_live` — liveness: process is running
- `/tmp/maker8_ready` — readiness: all bootstrap dependencies initialised
- `/tmp/maker8_status.json` — full runtime snapshot (JSON)

Use in Docker:

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import os; exit(0 if os.path.exists('/tmp/maker8_live') else 1)"]
  interval: 30s
  timeout: 5s
  retries: 3
```

### Local Development

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Start (requires Kafka + FFmpeg)
maker8

# Run tests
pytest

# Lint
ruff check src/
```

---

## Extending

### Add a Source Connector

1. Create `plugins/sources/<kind>.py` implementing `SourceConnectorPlugin`
2. Implement `manifest()`, `resolve()`, and `download()`
3. Register in `PluginRegistry.load_defaults()`

### Add an Effect Plugin

1. Create `plugins/effects/<name>.py` implementing `EffectPlugin`
2. Implement `manifest()` and `apply()`
3. Register in `PluginRegistry.load_defaults()` with ID `"effect:<name>"`

### Add a TTS Provider

1. Subclass `TTSProvider` in `services/tts_client.py`
2. Implement `synthesize(text, lang, output_path, **kwargs) → SynthesisResult`
3. Register in `TTSService._PROVIDERS`
4. Add preset entries to `config/tts_presets.json`

### Add a Pipeline Stage

1. Add enum value to `RenderStage` in `models/common.py`
2. Create `pipeline/<stage>.py` with a class extending `Stage`
3. Register in `pipeline/orchestrator.py` stage list
4. Add to `RENDER_RETRYABLE_STAGES` in `retry.py` if retryable

---

## Project Structure

```
src/maker8/
├── models/              # Pydantic v2 models
│   ├── common.py        #   Shared enums, ErrorInfo, DropboxFileRef, etc.
│   ├── spec.py          #   RenderSpec: Canvas, Scene, Layer, Asset, etc.
│   ├── contracts.py     #   RenderRequest, RenderResult, DLQPayload
│   └── manifest.py      #   Dropbox manifest
├── kafka/               # Kafka consumer / producer wrappers
│   ├── consumer.py      #   RenderConsumer (poll loop + deserialization)
│   └── producer.py      #   KafkaProducer (send + flush)
├── pipeline/            # Pipeline stages (one file per stage)
│   ├── context.py       #   PipelineContext (mutable state)
│   ├── stage.py         #   Stage ABC
│   ├── orchestrator.py  #   Chains stages, retry, DLQ
│   ├── validate.py      #   VALIDATE
│   ├── resolve.py       #   RESOLVE_ASSETS
│   ├── download.py      #   DOWNLOAD
│   ├── normalize.py     #   NORMALIZE
│   ├── tts.py           #   TTS
│   ├── render.py        #   RENDER (bridge to rendering/)
│   ├── upload.py        #   UPLOAD_DROPBOX
│   └── emit.py          #   EMIT_RESULT
├── plugins/             # Extensible plugin system
│   ├── base.py          #   ABCs: SourceConnectorPlugin, EffectPlugin
│   ├── registry.py      #   Singleton plugin registry
│   ├── sources/         #   Built-in source connectors
│   │   ├── youtube.py   #     YouTube/multi-site via yt-dlp
│   │   └── http_source.py #   Direct HTTP/HTTPS download
│   └── effects/         #   Built-in effects (10 plugins)
│       ├── fade.py, zoom_pan.py, blur.py, brightness_contrast.py,
│       ├── slide.py, color_overlay.py, grayscale.py, rotate.py,
│       ├── mirror.py, chroma_key.py
│       └── ...
├── rendering/           # MoviePy / Pillow video composition
│   ├── composer.py      #   Scenes → final video
│   ├── layers.py        #   Layer → MoviePy clip
│   └── text.py          #   Text rendering via Pillow
├── services/            # External service clients
│   ├── dropbox_client.py #  Upload to Dropbox (simple + session)
│   ├── tts_client.py    #   TTS facade + providers (gTTS, Google Cloud, ElevenLabs)
│   └── key_ring.py      #   Round-robin credential rotation
├── utils/               # Utilities
│   ├── logging.py       #   structlog setup
│   ├── hashing.py       #   SHA-256 + Dropbox content hash
│   └── versions.py      #   Engine version collection
├── canon.py             # Canonicalization + job_key computation
├── retry.py             # RetryPolicy, StageError, backoff
├── config.py            # Pydantic Settings (env vars)
└── app.py               # Entry point
```

### Documentation Files

```
docs/
├── schemas/
│   ├── render_request.schema.json   # Auto-generated JSON Schema
│   ├── render_result.schema.json
│   └── dlq_payload.schema.json
├── examples/
│   ├── render_request.example.json          # Full input example
│   ├── render_request_minimal.example.json  # Minimal input example
│   ├── render_result_success.example.json   # Successful result
│   ├── render_result_failed.example.json    # Failed result
│   └── dlq_payload.example.json             # DLQ message
├── maker8-specs.md              # Original specification (Vietnamese)
├── KAFKA_INTEGRATION.md         # Kafka setup guide
├── TTS_CREDENTIAL_ROTATION.md   # TTS key rotation guide
├── SEGFAULT_TROUBLESHOOTING.md  # FFmpeg/MoviePy troubleshooting
├── QUICK_REFERENCE.md           # Quick reference card
└── IMPLEMENTATION_STATUS.md     # Implementation progress
```

## License

Proprietary.
