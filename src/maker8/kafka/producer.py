"""Kafka producer for render results and DLQ messages."""

from __future__ import annotations

import json
from typing import Any

from confluent_kafka import Producer

from maker8.config import Settings
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class KafkaProducer:
    """Thin wrapper around ``confluent_kafka.Producer``."""

    def __init__(self, settings: Settings) -> None:
        # Build Kafka config
        kafka_config = {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
        }

        # Add SASL credentials if provided
        if settings.kafka_security_protocol:
            kafka_config["security.protocol"] = settings.kafka_security_protocol
        if settings.kafka_sasl_mechanism:
            kafka_config["sasl.mechanism"] = settings.kafka_sasl_mechanism
        if settings.kafka_username:
            kafka_config["sasl.username"] = settings.kafka_username
        if settings.kafka_password:
            kafka_config["sasl.password"] = settings.kafka_password

        self._producer = Producer(kafka_config)

    def send(self, topic: str, key: str, value: dict[str, Any]) -> None:
        """Serialise *value* as JSON and enqueue to *topic*.

        Does **not** flush; call :meth:`flush` or :meth:`close` explicitly to
        ensure delivery.  Flushing after every ``send()`` was removed because
        it blocked the pipeline on every result/DLQ emit — the consumer loop
        now calls :meth:`flush` periodically instead.
        """
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._producer.produce(
            topic,
            key=key.encode("utf-8"),
            value=payload,
            callback=self._on_delivery,
        )

    def flush(self, timeout: float = 5.0) -> None:
        """Deliver any buffered messages, waiting up to *timeout* seconds."""
        remaining = self._producer.flush(timeout)
        if remaining > 0:
            log.warning("producer.flush_timeout", remaining_messages=remaining, timeout=timeout)

    def close(self, timeout: float = 10.0) -> None:
        remaining = self._producer.flush(timeout)
        if remaining > 0:
            log.warning("producer.close_timeout", remaining_messages=remaining)

    # ── internal ─────────────────────────────────────────────────────

    @staticmethod
    def _on_delivery(err: object, msg: object) -> None:
        if err:
            log.error("producer.delivery_failed", error=str(err))
        else:
            log.debug("producer.delivered", topic=getattr(msg, "topic", lambda: "?")())
