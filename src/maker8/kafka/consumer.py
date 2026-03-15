"""Kafka consumer that drives the render pipeline."""

from __future__ import annotations

import json
from typing import Any, Callable

from confluent_kafka import Consumer, KafkaError, KafkaException, Message

from maker8.config import Settings
from maker8.observability.metrics import JOBS_RECEIVED, KAFKA_CONSUMER_RUNNING
from maker8.observability.state import WorkerState
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class RenderConsumer:
    """Subscribe to render-request topic and dispatch each message to *handler*.

    One message at a time – manual commit after successful handling.
    """

    def __init__(
        self,
        settings: Settings,
        worker_state: WorkerState | None = None,
    ) -> None:
        self._settings = settings
        self._state = worker_state
        
        # Build Kafka config
        kafka_config = {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.kafka_group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            # Allow enough time for the full synchronous pipeline to complete
            # (resolve + download + TTS + render + upload) before the broker
            # considers this consumer dead and revokes partition ownership.
            "max.poll.interval.ms": settings.kafka_max_poll_interval_ms,
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

    def start(self, handler: Callable[[dict[str, Any]], None]) -> None:
        """Block forever, calling *handler(payload)* for each message."""
        topic = self._settings.kafka_render_request_topic
        self._consumer.subscribe([topic])
        self._running = True
        KAFKA_CONSUMER_RUNNING.set(1)
        if self._state:
            self._state.consumer_running = True
        log.info("consumer.started", topic=topic)

        while self._running:
            msg: Message | None = self._consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    log.debug(
                        "consumer.partition_eof",
                        partition=msg.partition(),
                        offset=msg.offset(),
                    )
                    continue
                log.error(
                    "consumer.error",
                    error=str(msg.error()),
                    code=msg.error().code(),
                )
                raise KafkaException(msg.error())

            # ── Message received ─────────────────────────────────────
            partition = msg.partition()
            offset = msg.offset()
            msg_key = msg.key().decode("utf-8", errors="replace") if msg.key() else ""
            payload_size = len(msg.value()) if msg.value() else 0

            log.info(
                "consumer.message_received",
                topic=topic,
                partition=partition,
                offset=offset,
                key=msg_key,
                payload_size=payload_size,
            )
            JOBS_RECEIVED.inc()
            if self._state:
                self._state.on_message_received(partition=partition, offset=offset)

            try:
                payload = json.loads(msg.value().decode("utf-8"))
                log.info(
                    "consumer.handler_started",
                    partition=partition,
                    offset=offset,
                    key=msg_key,
                )
                handler(payload)
                log.info(
                    "consumer.handler_finished",
                    partition=partition,
                    offset=offset,
                    key=msg_key,
                    result="success",
                )
            except Exception:
                log.exception(
                    "consumer.handler_error",
                    partition=partition,
                    offset=offset,
                    key=msg_key,
                )
            finally:
                try:
                    self._consumer.commit(msg)
                    log.debug(
                        "consumer.commit_succeeded",
                        partition=partition,
                        offset=offset,
                    )
                except Exception:
                    log.exception(
                        "consumer.commit_failed",
                        offset=offset,
                        partition=partition,
                    )

    # ── Lifecycle ────────────────────────────────────────────────────

    def stop(self) -> None:
        self._running = False
        KAFKA_CONSUMER_RUNNING.set(0)
        if self._state:
            self._state.consumer_running = False
        self._consumer.close()
        log.info("consumer.stopped")
