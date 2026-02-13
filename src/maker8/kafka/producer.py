"""Kafka producer for render results and DLQ messages."""

from __future__ import annotations

import json

from confluent_kafka import Producer

from maker8.config import Settings
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class KafkaProducer:
    """Thin wrapper around ``confluent_kafka.Producer``."""

    def __init__(self, settings: Settings) -> None:
        self._producer = Producer(
            {"bootstrap.servers": settings.kafka_bootstrap_servers}
        )

    def send(self, topic: str, key: str, value: dict) -> None:
        """Serialise *value* as JSON and produce to *topic*."""
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._producer.produce(
            topic,
            key=key.encode("utf-8"),
            value=payload,
            callback=self._on_delivery,
        )
        self._producer.flush()

    def close(self) -> None:
        self._producer.flush()

    # ── internal ─────────────────────────────────────────────────────

    @staticmethod
    def _on_delivery(err: object, msg: object) -> None:
        if err:
            log.error("producer.delivery_failed", error=str(err))
        else:
            log.debug("producer.delivered", topic=getattr(msg, "topic", lambda: "?")())
