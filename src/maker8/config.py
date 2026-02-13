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

    # ── Dropbox ──────────────────────────────────────────────────────
    dropbox_app_key: str = ""
    dropbox_app_secret: str = ""
    dropbox_refresh_token: str = ""

    # ── TTS ───────────────────────────────────────────────────────────
    tts_provider: str = "gtts"
    tts_presets_path: Path = Path("config/tts_presets.json")

    # ── Google Cloud TTS ─────────────────────────────────────────────
    # Uses Application Default Credentials (ADC) or GOOGLE_APPLICATION_CREDENTIALS
    google_cloud_tts_enabled: bool = False

    # ── ElevenLabs TTS ───────────────────────────────────────────────
    elevenlabs_api_key: str = ""

    # ── Work directory ───────────────────────────────────────────────
    work_dir: Path = Path("/tmp/maker8")

    # ── Retry ────────────────────────────────────────────────────────
    render_max_attempts: int = 5
    render_retry_min_delay_sec: float = 60.0
    render_retry_max_delay_sec: float = 21600.0  # 6 h

    # ── Logging ──────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "json"


def get_settings() -> Settings:
    """Factory – import once, call where needed."""
    return Settings()
