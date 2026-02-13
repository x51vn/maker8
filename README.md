# Maker8 – Video Render Pipeline

Scene-based video rendering pipeline that consumes Kafka messages, ingests media, generates TTS narration, composes video via MoviePy/FFmpeg, and uploads results to Dropbox.

## Quick Start

```bash
# Install in development mode
pip install -e ".[dev]"

# Run locally (requires Kafka + FFmpeg)
maker8

# Or with Docker Compose
docker compose up --build
```

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
- Dead-letter queue for exhausted retries or non-retryable failures

## Configuration

All settings are loaded from environment variables prefixed with `MAKER8_`:

| Variable | Default | Description |
|---|---|---|
| `MAKER8_KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka bootstrap servers |
| `MAKER8_KAFKA_GROUP_ID` | `maker8-render` | Consumer group ID |
| `MAKER8_DROPBOX_APP_KEY` | | Dropbox OAuth app key |
| `MAKER8_DROPBOX_APP_SECRET` | | Dropbox OAuth app secret |
| `MAKER8_DROPBOX_REFRESH_TOKEN` | | Dropbox OAuth refresh token |
| `MAKER8_TTS_PROVIDER` | `gtts` | Default TTS provider |
| `MAKER8_WORK_DIR` | `/tmp/maker8` | Temporary work directory |
| `MAKER8_LOG_LEVEL` | `INFO` | Log level |
| `MAKER8_LOG_FORMAT` | `json` | `json` or `console` |

## Project Structure

```
src/maker8/
├── models/       # Pydantic v2 models (common → spec → contracts → manifest)
├── kafka/        # Kafka consumer / producer wrappers
├── pipeline/     # Pipeline stages (one file per stage)
├── plugins/      # Extensible plugin system (source connectors, effects)
├── rendering/    # MoviePy / Pillow video composition
├── services/     # External service clients (Dropbox, TTS)
├── utils/        # Hashing, structured logging
├── canon.py      # Canonicalization + job key
├── retry.py      # Retry policy + backoff
├── config.py     # Runtime configuration
└── app.py        # Entry point
```

## Extending

### Add a Source Connector

1. Create `plugins/sources/<kind>.py` implementing `SourceConnectorPlugin`
2. Register in `PluginRegistry.load_defaults()`

### Add a TTS Provider

1. Subclass `TTSProvider` in `services/tts_client.py`
2. Register in `TTSService._PROVIDERS`
3. Add presets to `config/tts_presets.json`

### Add a Pipeline Stage

1. Add enum value to `RenderStage` in `models/common.py`
2. Create `pipeline/<stage>.py` with a class extending `Stage`
3. Register in `pipeline/orchestrator.py`

## License

Proprietary.
