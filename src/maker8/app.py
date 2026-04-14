"""Maker8 Render Worker – application entry point.

Start with::

    python -m maker8.app
    # or
    maker8              (if installed via pip)
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
from pathlib import Path

from maker8.config import Settings, get_settings
from maker8.kafka.consumer import RenderConsumer
from maker8.kafka.producer import KafkaProducer
from maker8.observability.health import HealthManager
from maker8.observability.metrics import WORKER_READY, WORKER_UP
from maker8.observability.state import WorkerState
from maker8.pipeline.orchestrator import Orchestrator
from maker8.plugins.registry import PluginRegistry
from maker8.plugins.sources.youtube import probe_ytdlp, resolve_ytdlp_path
from maker8.rendering.encoder import probe_gpu_capabilities
from maker8.rendering.ffmpeg_runtime import bind_moviepy_ffmpeg, diagnose_runtime
from maker8.services.credential_reader import CredentialReader
from maker8.services.dropbox_client import DropboxClient
from maker8.services.tts_client import TTSService
from maker8.services.ytdlp_updater import UpdaterConfig, YtdlpUpdater
from maker8.utils.logging import get_logger, setup_logging

# Global variables for shutdown coordination
_shutdown_requested = False
_consumer: RenderConsumer | None = None
_producer: KafkaProducer | None = None
_health: HealthManager | None = None
_updater: YtdlpUpdater | None = None
_log: object | None = None


def _write_secret_file(path: Path, content: str) -> Path:
    """Write secret text to a private file and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _apply_db_runtime_overrides(
    settings: Settings,
    credential_reader: CredentialReader,
    log: object,
) -> list[str]:
    """Load runtime secrets from DB and apply them to settings."""
    errors: list[str] = []

    # Disable env/file fallback for secrets in DB mode.
    settings.kafka_username = ""
    settings.kafka_password = ""
    settings.elevenlabs_api_key = ""
    settings.ytdlp_cookies_file = ""
    settings.ytdlp_cookies_from_browser = ""

    # Kafka SASL credentials (maker8 scope)
    kafka_username = credential_reader.get_first_key("kafka_maker8_username")
    kafka_password = credential_reader.get_first_key("kafka_maker8_password")
    if kafka_username:
        settings.kafka_username = kafka_username
    if kafka_password:
        settings.kafka_password = kafka_password

    if settings.kafka_security_protocol and (
        not settings.kafka_username or not settings.kafka_password
    ):
        errors.append(
            "Kafka SASL is enabled but 'kafka_maker8_username' / "
            "'kafka_maker8_password' are missing in editor8 service_keys."
        )

    # yt-dlp auth material (optional)
    cookies_raw = credential_reader.get_first_key("ytdlp_cookies")
    if cookies_raw:
        cookies_path = _write_secret_file(
            settings.work_dir / "credentials" / "ytdlp" / "cookies.txt",
            cookies_raw,
        )
        settings.ytdlp_cookies_file = str(cookies_path)
    else:
        cookies_from_browser = credential_reader.get_first_key("ytdlp_cookies_from_browser")
        if cookies_from_browser:
            settings.ytdlp_cookies_from_browser = cookies_from_browser.strip()

    log.info(
        "app.db_runtime_overrides_applied",
        has_kafka_username=bool(settings.kafka_username),
        has_kafka_password=bool(settings.kafka_password),
        ytdlp_cookies_file_set=bool(settings.ytdlp_cookies_file),
        ytdlp_cookies_from_browser_set=bool(settings.ytdlp_cookies_from_browser),
    )

    return errors


def main() -> None:
    global _consumer, _producer, _health, _updater, _log, _shutdown_requested

    settings = get_settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("maker8.app")
    _log = log

    log.info("app.starting", version="0.1.0")

    # ── FFmpeg runtime resolution ────────────────────────────────────
    # Must run before any MoviePy import to ensure a single binary.
    bind_moviepy_ffmpeg()
    rt = diagnose_runtime()
    log.info(
        "app.ffmpeg_runtime",
        render_ffmpeg_path=rt.render_ffmpeg_path,
        render_ffmpeg_version=rt.render_ffmpeg_version,
        system_ffmpeg_path=rt.system_ffmpeg_path,
        same_binary=rt.same_binary,
        imageio_ffmpeg_path=rt.imageio_ffmpeg_path,
        render_nvenc_available=rt.render_nvenc_available,
    )

    # ── GPU capability probe ─────────────────────────────────────────
    gpu = probe_gpu_capabilities()
    log.info(
        "app.gpu_capabilities",
        nvidia_smi=gpu.nvidia_smi,
        nvenc_available=gpu.nvenc_available,
        cuda_hwaccel=gpu.cuda_hwaccel,
        gpu_render_enabled=gpu.gpu_render_enabled,
    )

    # ── yt-dlp startup validation ────────────────────────────────────
    ytdlp_exe = resolve_ytdlp_path(settings.ytdlp_path, settings.ytdlp_bin_dir)
    ytdlp_ver = probe_ytdlp(ytdlp_exe)
    if ytdlp_ver:
        log.info("app.ytdlp_ready", executable=ytdlp_exe, version=ytdlp_ver)
    else:
        log.warning(
            "app.ytdlp_not_found",
            executable=ytdlp_exe,
            msg="yt-dlp is not callable — YouTube assets will fail at resolve",
        )

    # ── Observability bootstrap ──────────────────────────────────────
    worker_state = WorkerState()
    health = HealthManager(
        state=worker_state,
        status_path=Path(settings.status_file),
    )
    _health = health
    health.mark_live()
    # Auto-flush status.json on every state change
    worker_state.set_on_change(health.flush_status)
    WORKER_UP.set(1)
    log.info("app.liveness_ready")

    # Start Prometheus metrics server if enabled
    if settings.metrics_enabled:
        from prometheus_client import start_http_server

        start_http_server(settings.metrics_port)
        log.info("app.metrics_server_started", port=settings.metrics_port)

    # ── Credential source setup ──────────────────────────────────────
    credential_reader: CredentialReader | None = None
    if settings.credential_source == "db":
        if not settings.editor8_database_url:
            log.critical(
                "app.credential_source_db_no_url",
                msg=(
                    "MAKER8_CREDENTIAL_SOURCE=db but MAKER8_EDITOR8_DATABASE_URL "
                    "is not set. Set it to editor8's PostgreSQL URL."
                ),
            )
            health.mark_not_live()
            os._exit(1)

        log.info(
            "app.credential_source_db",
            ttl_sec=settings.credential_cache_ttl_sec,
        )
        credential_reader = CredentialReader(
            settings.editor8_database_url,
            ttl_sec=settings.credential_cache_ttl_sec,
        )
        # Fail-fast: probe DB connectivity and required credentials now.
        missing = credential_reader.readiness_check()
        if missing:
            for msg in missing:
                log.critical("app.missing_required_credential", msg=msg)
            health.mark_not_live()
            os._exit(1)

        override_errors = _apply_db_runtime_overrides(settings, credential_reader, log)
        if override_errors:
            for msg in override_errors:
                log.critical("app.missing_required_credential", msg=msg)
            health.mark_not_live()
            os._exit(1)
        log.info("app.credential_reader_ready")

    # ── Wire dependencies ────────────────────────────────────────────
    producer = KafkaProducer(settings)
    _producer = producer

    registry = PluginRegistry()
    registry.load_defaults(settings)

    tts_service = TTSService(settings, credential_reader=credential_reader)

    # ── M5: Warn if no TTS credentials detected on startup ──────────
    # In DB mode, has_provider() enforces active keys in editor8 DB
    # for google_cloud/elevenlabs. In env_file mode, legacy fallbacks
    # (ADC/single env key) are still supported.
    # We log a warning rather than hard-exiting because:
    #  (a) preset-based selection may route some jobs to gtts even when the
    #      default provider has no keys, and
    #  (b) incorrect startup kills obscure the real error at synthesis time.
    if not tts_service.has_provider():
        log.warning(
            "app.no_tts_provider_warning",
            default_provider=tts_service._default_provider,  # noqa: SLF001
            msg=(
                "Default TTS provider has no credentials; jobs that require"
                " it will fail at the TTS stage. Configure keys or switch provider."
            ),
        )

    try:
        dbx_client = DropboxClient(settings, credential_reader=credential_reader)
    except RuntimeError:
        log.critical(
            "app.dropbox_auth_failed",
            msg="Cannot start without valid Dropbox credentials",
        )
        health.mark_not_live()
        os._exit(1)

    orchestrator = Orchestrator(
        settings=settings,
        producer=producer,
        registry=registry,
        tts_service=tts_service,
        dbx_client=dbx_client,
        worker_state=worker_state,
    )

    consumer = RenderConsumer(
        settings,
        worker_state=worker_state,
        dlq_producer=producer.send,
    )
    _consumer = consumer

    # ── yt-dlp auto-updater ──────────────────────────────────────────
    updater_cfg = UpdaterConfig(
        enabled=settings.ytdlp_auto_update_enabled,
        channel=settings.ytdlp_channel,
        bin_dir=settings.ytdlp_bin_dir,
        interval_sec=settings.ytdlp_update_interval_sec,
        download_timeout=settings.ytdlp_download_timeout,
        verify_checksum=settings.ytdlp_verify_checksum,
        min_check_interval_sec=settings.ytdlp_min_check_interval_sec,
    )
    updater = YtdlpUpdater(config=updater_cfg, worker_state=worker_state)
    _updater = updater
    updater.start()

    # Mark ready – all components wired
    health.mark_ready()
    WORKER_READY.set(1)
    log.info("app.ready")

    # ── Fast exit handler (fallback for graceful shutdown hang) ───────
    def _atexit() -> None:
        """Run at exit to close resources and log completion."""
        if _updater is not None:
            _updater.stop()
        if _health is not None:
            _health.cleanup()
        if _log is not None:
            try:
                _log.info("app.exiting")
            except Exception as _e:
                # Logger itself failed (e.g. broken pipe on shutdown).
                # Print to stderr so the error is not silently lost.
                print(f"[maker8] app.exiting log failed: {_e}", file=sys.stderr)

    atexit.register(_atexit)

    # ── Graceful shutdown ────────────────────────────────────────────
    def _shutdown(sig: int, _frame: object) -> None:
        global _shutdown_requested

        if _shutdown_requested:
            # Already shutting down, force exit
            if _log is not None:
                _log.warning("app.force_exit", reason="shutdown_already_in_progress")
            os._exit(0)

        _shutdown_requested = True

        if _log is not None:
            _log.info("app.shutdown", signal=sig)

        WORKER_READY.set(0)
        if _health is not None:
            _health.mark_not_ready()

        try:
            if _consumer is not None:
                _consumer.stop()
        except Exception as e:
            if _log is not None:
                _log.error("consumer.stop_failed", error=str(e))

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Run ──────────────────────────────────────────────────────────
    try:
        consumer.start(handler=orchestrator.handle)
    except KeyboardInterrupt:
        pass
    finally:
        WORKER_UP.set(0)
        WORKER_READY.set(0)
        if _health is not None:
            _health.mark_not_live()
            _health.mark_not_ready()

        try:
            producer.close(timeout=10.0)
            log.info("app.stopped")
        except Exception as e:
            log.error("producer.close_failed", error=str(e))

        # Use os._exit to avoid C extension cleanup issues on shutdown
        # See: https://github.com/confluentinc/confluent-kafka-python/issues/...
        _shutdown_requested = True
        os._exit(0)


if __name__ == "__main__":
    main()
