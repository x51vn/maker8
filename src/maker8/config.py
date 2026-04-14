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
    # (yt-dlp resolve 120 s + download 600 s + TTS + render + upload ≈ 60–90 min
    # for long CPU-only jobs on large AV1 assets).
    kafka_max_poll_interval_ms: int = 7_200_000  # 2 hours

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

    # ── Performance ──────────────────────────────────────────────────
    # "quality" | "balanced" | "fast" – controls proxy resolution,
    # fps cap, effect allowance, and encode presets.
    perf_mode: str = "balanced"
    # Maximum short-edge resolution for proxy assets.
    # 0 = auto (derive from canvas size + perf_mode).
    proxy_max_resolution: int = 0

    # ── Retry ────────────────────────────────────────────────────────
    render_max_attempts: int = 5
    render_retry_min_delay_sec: float = 60.0
    render_retry_max_delay_sec: float = 21600.0  # 6 h

    # ── Logging ──────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "console"

    # ── Metrics ─────────────────────────────────────────────────────
    metrics_enabled: bool = False
    metrics_port: int = 9108

    # ── Health files ────────────────────────────────────────────────
    status_file: str = "/tmp/maker8_status.json"

    # ── yt-dlp ──────────────────────────────────────────────────────
    ytdlp_path: str = ""  # empty → auto-detect from managed dir or PATH
    ytdlp_bin_dir: Path = Path("/opt/maker8/bin/yt-dlp")
    ytdlp_cookies_file: str = ""
    ytdlp_cookies_from_browser: str = ""
    ytdlp_user_agent: str = ""
    ytdlp_extractor_args: str = ""
    ytdlp_verbose_on_failure: bool = True
    ytdlp_resolve_timeout_sec: int = 120
    ytdlp_download_timeout_sec: int = 600
    # Auto-update
    ytdlp_auto_update_enabled: bool = False
    ytdlp_channel: str = "stable"  # "stable" | "nightly"
    ytdlp_update_interval_sec: int = 21600  # 6 hours
    ytdlp_download_timeout: int = 120
    ytdlp_verify_checksum: bool = True
    ytdlp_min_check_interval_sec: int = 300

    # ── Centralized credential management ───────────────────────────
    # "db"       = read credentials from editor8's PostgreSQL database (default)
    # "env_file" = legacy fallback (read keys from env-vars / key-directories)
    credential_source: str = "db"
    editor8_database_url: str = ""  # required when credential_source == "db"
    credential_cache_ttl_sec: float = 60.0  # how long to cache DB credentials


def get_settings() -> Settings:
    """Factory – import once, call where needed."""
    return Settings()
