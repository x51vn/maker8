# ── Build ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir build \
    && python -m build --wheel --outdir /dist

# ── Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

# FFmpeg + yt-dlp runtime deps + CA certs for HTTPS downloads
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsm6 libxext6 libxrender1 \
        ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno – required by yt-dlp for full YouTube format extraction.
# Without it, yt-dlp falls back to Android VR API which only offers AV1
# streams, causing expensive CPU transcodes during normalisation.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

WORKDIR /app

COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl \
    && yt-dlp --version

# Create managed yt-dlp directory and seed with pip-installed version
RUN mkdir -p /opt/maker8/bin/yt-dlp \
    && cp "$(which yt-dlp)" /opt/maker8/bin/yt-dlp/current \
    && chmod +x /opt/maker8/bin/yt-dlp/current

# Default TTS presets & env template
COPY config/ config/
COPY .env.example .env.example

ENV MAKER8_WORK_DIR=/data/maker8
# Force MoviePy / imageio-ffmpeg to use the system-installed FFmpeg
# binary (which has NVENC support) instead of the bundled static build.
ENV IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg
# Ensure the NVIDIA container runtime exposes GPU devices and capabilities.
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,video,utility
VOLUME /data/maker8

ENTRYPOINT ["maker8"]
