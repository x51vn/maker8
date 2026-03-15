"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All Maker8 Render Worker settings.

    Every field can be overridden via env var prefixed with ``MAKER8_``.
    """

    model_config = SettingsConfigDict(
        env_prefix="MAKER8_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Kafka ────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_group_id: str = "maker8-render"
    kafka_render_request_topic: str = "video.render.request.v1"
    kafka_render_result_topic: str = "video.render.result.v1"
    kafka_render_dlq_topic: str = "video.render.dlq.v1"
    # SASL authentication (optional, leave empty if not required)
    kafka_username: str = ""
    kafka_password: str = ""
    kafka_security_protocol: str = ""  # e.g., "SASL_PLAINTEXT", "SASL_SSL"
    kafka_sasl_mechanism: str = ""  # e.g., "PLAIN"
    # Max time (ms) between two consumer poll() calls before broker considers
    # the consumer dead.  Must exceed the worst-case pipeline duration
    # (yt-dlp resolve 120 s + download 600 s + TTS + render + upload ≈ 30 min).
    kafka_max_poll_interval_ms: int = 1_800_000  # 30 minutes

    # ── Dropbox ──────────────────────────────────────────────────────
    dropbox_app_key: str = ""
    dropbox_app_secret: str = ""
    dropbox_refresh_token: str = ""

    # ── TTS ───────────────────────────────────────────────────────────
    tts_provider: str = "gtts"
    tts_presets_path: Path = Path("config/tts_presets.json")

    # ── Google Cloud TTS ─────────────────────────────────────────────

    # Directory with service-account JSON files for round-robin rotation
    google_tts_keys_dir: Path = Path("gg-tts-keys")

    # ── ElevenLabs TTS ───────────────────────────────────────────────
    # Single-key fallback
    elevenlabs_api_key: str = ""
    # Directory with API key files (.txt/.key) for round-robin rotation
    elevenlabs_keys_dir: Path = Path("elevenlabs-keys")

    # ── TTS timeout ──────────────────────────────────────────────────
    # Maximum seconds to wait for a single TTS synthesis call.
    # Prevents the worker from hanging indefinitely on network stalls.
    tts_timeout_sec: float = 120.0

    # ── Work directory ───────────────────────────────────────────────
    work_dir: Path = Path("/tmp/maker8")

    # ── Retry ────────────────────────────────────────────────────────
    render_max_attempts: int = 5
    render_retry_min_delay_sec: float = 60.0
    render_retry_max_delay_sec: float = 21600.0  # 6 h

    # ── Logging ──────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "json"

    # ── Metrics ─────────────────────────────────────────────────────
    metrics_enabled: bool = False
    metrics_port: int = 9108

    # ── Health files ────────────────────────────────────────────────
    status_file: str = "/tmp/maker8_status.json"


def get_settings() -> Settings:
    """Factory – import once, call where needed."""
    return Settings()
