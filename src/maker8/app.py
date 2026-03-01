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
from maker8.pipeline.orchestrator import Orchestrator
from maker8.plugins.registry import PluginRegistry
from maker8.services.dropbox_client import DropboxClient
from maker8.services.tts_client import TTSService
from maker8.utils.logging import get_logger, setup_logging


# Global variables for shutdown coordination
_shutdown_requested = False
_consumer: RenderConsumer | None = None
_producer: KafkaProducer | None = None
_log: object | None = None


def main() -> None:
    global _consumer, _producer, _log, _shutdown_requested
    
    settings = get_settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("maker8.app")
    _log = log

    log.info("app.starting", version="0.1.0")

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
    )

    consumer = RenderConsumer(settings)
    _consumer = consumer

    # ── Fast exit handler (fallback for graceful shutdown hang) ───────
    def _atexit() -> None:
        """Run at exit to close resources and log completion."""
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
        # Write health file so Docker healthcheck can verify the app is running
        _health_file = Path("/tmp/maker8_healthy")
        try:
            _health_file.touch()
            log.info("app.health_file_created", path=str(_health_file))
        except Exception as _e:
            log.warning("app.health_file_failed", path=str(_health_file), error=str(_e))

        consumer.start(handler=orchestrator.handle)
    except KeyboardInterrupt:
        pass
    finally:
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
