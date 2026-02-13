# ── Build ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir build \
    && python -m build --wheel --outdir /dist

# ── Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

# FFmpeg + yt-dlp runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Default TTS presets
COPY config/ config/

ENV MAKER8_WORK_DIR=/data/maker8
VOLUME /data/maker8

ENTRYPOINT ["maker8"]
