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

from maker8.config import get_settings
from maker8.kafka.consumer import RenderConsumer
from maker8.kafka.producer import KafkaProducer
from maker8.observability.health import HealthManager
from maker8.observability.metrics import WORKER_READY, WORKER_UP
from maker8.observability.state import WorkerState
from maker8.pipeline.orchestrator import Orchestrator
from maker8.plugins.registry import PluginRegistry
from maker8.rendering.encoder import probe_gpu_capabilities
from maker8.services.dropbox_client import DropboxClient
from maker8.services.tts_client import TTSService
from maker8.utils.logging import get_logger, setup_logging


# Global variables for shutdown coordination
_shutdown_requested = False
_consumer: RenderConsumer | None = None
_producer: KafkaProducer | None = None
_health: HealthManager | None = None
_log: object | None = None


def main() -> None:
    global _consumer, _producer, _health, _log, _shutdown_requested

    settings = get_settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("maker8.app")
    _log = log

    log.info("app.starting", version="0.1.0")

    # ── GPU capability probe ─────────────────────────────────────────
    gpu = probe_gpu_capabilities()
    log.info(
        "app.gpu_capabilities",
        nvidia_smi=gpu.nvidia_smi,
        nvenc_available=gpu.nvenc_available,
        cuda_hwaccel=gpu.cuda_hwaccel,
        gpu_render_enabled=gpu.gpu_render_enabled,
    )

    # ── Observability bootstrap ──────────────────────────────────────
    worker_state = WorkerState()
    health = HealthManager(
        state=worker_state,
        status_path=Path(settings.status_file),
    )
    _health = health
    health.mark_live()
    WORKER_UP.set(1)
    log.info("app.liveness_ready")

    # Start Prometheus metrics server if enabled
    if settings.metrics_enabled:
        from prometheus_client import start_http_server

        start_http_server(settings.metrics_port)
        log.info("app.metrics_server_started", port=settings.metrics_port)

    # ── Wire dependencies ────────────────────────────────────────────
    producer = KafkaProducer(settings)
    _producer = producer

    registry = PluginRegistry()
    registry.load_defaults()

    tts_service = TTSService(settings)
    dbx_client = DropboxClient(settings)

    orchestrator = Orchestrator(
        settings=settings,
        producer=producer,
        registry=registry,
        tts_service=tts_service,
        dbx_client=dbx_client,
        worker_state=worker_state,
    )

    consumer = RenderConsumer(settings, worker_state=worker_state)
    _consumer = consumer

    # Mark ready – all components wired
    health.mark_ready()
    WORKER_READY.set(1)
    log.info("app.ready")

    # ── Fast exit handler (fallback for graceful shutdown hang) ───────
    def _atexit() -> None:
        """Run at exit to close resources and log completion."""
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
            producer.close()
            log.info("app.stopped")
        except Exception as e:
            log.error("producer.close_failed", error=str(e))

        # Use os._exit to avoid C extension cleanup issues on shutdown
        # See: https://github.com/confluentinc/confluent-kafka-python/issues/...
        _shutdown_requested = True
        os._exit(0)


if __name__ == "__main__":
    main()
