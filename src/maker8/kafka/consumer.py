"""Kafka consumer that drives the render pipeline."""

from __future__ import annotations

import json
from typing import Callable

from confluent_kafka import Consumer, KafkaError, KafkaException, Message

from maker8.config import Settings
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class RenderConsumer:
    """Subscribe to render-request topic and dispatch each message to *handler*.

    One message at a time – manual commit after successful handling.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        
        # Build Kafka config
        kafka_config = {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.kafka_group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
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
        
        self._consumer = Consumer(kafka_config)
        self._running = False

    # ── Main loop ────────────────────────────────────────────────────

    def start(self, handler: Callable[[dict], None]) -> None:
        """Block forever, calling *handler(payload)* for each message."""
        topic = self._settings.kafka_render_request_topic
        self._consumer.subscribe([topic])
        self._running = True
        log.info("consumer.started", topic=topic)

        while self._running:
            msg: Message | None = self._consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                log.error("consumer.error", error=str(msg.error()))
                raise KafkaException(msg.error())

            try:
                payload = json.loads(msg.value().decode("utf-8"))
                handler(payload)
            except Exception:
                log.exception("consumer.handler_error", offset=msg.offset())
            finally:
                # Always commit to avoid infinite re-processing
                self._consumer.commit(msg)

    # ── Lifecycle ────────────────────────────────────────────────────

    def stop(self) -> None:
        self._running = False
        self._consumer.close()
        log.info("consumer.stopped")
