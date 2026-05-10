# Maker8 overview

Maker8 is a Python 3.11 video render worker. It consumes Kafka render requests, validates and resolves assets, downloads media, normalizes it, generates TTS, renders video with MoviePy/FFmpeg, uploads to Dropbox, and emits result/DLQ messages back to Kafka.

Key layout:
- `src/maker8/`: worker implementation (config, Kafka, pipeline, rendering, services, observability, plugins)
- `src/render_contracts/`: canonical wire-format models shared with editor8
- `docs/schemas/` and `docs/examples/`: derived contract artifacts
- `tests/`: contract, pipeline, rendering, and integration-style tests
- `openspec/changes/`: spec-driven change workflow artifacts

Entry point: `maker8` or `python -m maker8.app`.