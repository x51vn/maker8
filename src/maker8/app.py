"""Maker8 Render Worker – application entry point.

Start with::

    python -m maker8.app
    # or
    maker8              (if installed via pip)
"""

from __future__ import annotations

import signal

from maker8.config import get_settings
from maker8.kafka.consumer import RenderConsumer
from maker8.kafka.producer import KafkaProducer
from maker8.pipeline.orchestrator import Orchestrator
from maker8.plugins.registry import PluginRegistry
from maker8.services.dropbox_client import DropboxClient
from maker8.services.tts_client import TTSService
from maker8.utils.logging import get_logger, setup_logging


def main() -> None:
    settings = get_settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("maker8.app")

    log.info("app.starting", version="0.1.0")

    # ── Wire dependencies ────────────────────────────────────────────
    producer = KafkaProducer(settings)

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

    # ── Graceful shutdown ────────────────────────────────────────────
    def _shutdown(sig: int, _frame: object) -> None:
        log.info("app.shutdown", signal=sig)
        consumer.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Run ──────────────────────────────────────────────────────────
    try:
        consumer.start(handler=orchestrator.handle)
    except KeyboardInterrupt:
        pass
    finally:
        producer.close()
        log.info("app.stopped")


if __name__ == "__main__":
    main()
