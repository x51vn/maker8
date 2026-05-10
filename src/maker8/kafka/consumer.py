"""Kafka consumer that drives the render pipeline."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException, Message

from maker8.config import Settings
from maker8.models.common import ErrorInfo
from maker8.models.contracts import DLQPayload
from maker8.observability.metrics import INVALID_PAYLOAD, JOBS_RECEIVED, KAFKA_CONSUMER_RUNNING
from maker8.observability.state import WorkerState
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_MAX_RAW_PAYLOAD_BYTES = 4096  # truncate oversized raw payloads in DLQ


class RenderConsumer:
    """Subscribe to render-request topic and dispatch each message to *handler*.

    One message at a time – manual commit after successful handling.

    Args:
        settings: Application settings.
        worker_state: Optional worker state for observability.
        dlq_producer: Optional callable ``(topic, key, payload) -> None`` used
            to emit DLQ messages for invalid-JSON poison pills.  When provided,
            every message whose bytes fail JSON parsing is routed to the DLQ
            before committing the offset, so no message is silently discarded.
    """

    def __init__(
        self,
        settings: Settings,
        worker_state: WorkerState | None = None,
        dlq_producer: Callable[[str, str, dict[str, Any]], None] | None = None,
        flush_producer: Callable[[], None] | None = None,
    ) -> None:
        self._settings = settings
        self._state = worker_state
        self._dlq_producer = dlq_producer
        self._flush_producer = flush_producer
        self._flush_interval: int = settings.producer_flush_interval

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

    def start(
        self,
        handler: Callable[[dict[str, Any]], None],
        stop_event: threading.Event | None = None,
    ) -> None:
        """Block forever, calling *handler(payload)* for each message.

        Args:
            handler: Called with the deserialized JSON payload for each message.
            stop_event: Optional :class:`threading.Event`.  When set, the poll
                loop breaks cleanly on the next iteration (within one poll
                timeout).  Used by the double-SIGINT shutdown path so an
                in-progress handler can finish before the loop exits.
        """
        topic = self._settings.kafka_render_request_topic
        self._consumer.subscribe([topic])
        self._running = True
        KAFKA_CONSUMER_RUNNING.set(1)
        if self._state:
            self._state.consumer_running = True
        log.info("consumer.started", topic=topic)

        last_flush_time: float = time.monotonic()

        while self._running:
            if stop_event is not None and stop_event.is_set():
                break
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
            except json.JSONDecodeError as exc:
                log.exception(
                    "consumer.invalid_json",
                    partition=partition,
                    offset=offset,
                    key=msg_key,
                )
                # Poison pill: emit DLQ (if producer wired) then commit
                self._emit_invalid_json_dlq(msg, msg_key, exc, partition, offset)
            except Exception:
                log.exception(
                    "consumer.handler_error",
                    partition=partition,
                    offset=offset,
                    key=msg_key,
                )
                # Handler errors are already handled by Orchestrator
                # (DLQ sent, result emitted).  Commit to advance offset.

            # Always commit after processing attempt to avoid reprocessing.
            # The Orchestrator handles DLQ/result emission internally –
            # the consumer's job is to advance the offset once the handler
            # has returned (whether successfully or with an error).
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

            # ── Periodic producer flush ───────────────────────────────
            if self._flush_producer is not None:
                now = time.monotonic()
                if self._flush_interval == 0 or (now - last_flush_time) >= self._flush_interval:
                    try:
                        self._flush_producer()
                    except Exception:
                        log.exception("consumer.flush_producer_error")
                    last_flush_time = now

    # ── DLQ helpers ──────────────────────────────────────────────────

    def _emit_invalid_json_dlq(
        self,
        msg: Message,
        msg_key: str,
        exc: json.JSONDecodeError,
        partition: int,
        offset: int,
    ) -> None:
        """Best-effort DLQ emission for messages that fail JSON parsing."""
        if self._dlq_producer is None:
            return
        try:
            raw = msg.value() or b""
            raw_snippet = raw[:_MAX_RAW_PAYLOAD_BYTES].decode("utf-8", errors="replace")
            job_id = msg_key or "unknown"
            dlq = DLQPayload(
                job_id=job_id,
                failed_stage="CONSUMER",
                attempts=0,
                max_attempts=0,
                last_error=ErrorInfo(
                    code="INVALID_JSON",
                    stage="CONSUMER",
                    retryable=False,
                    message=str(exc)[:2000],
                ),
                debug_context={
                    "raw_payload_snippet": raw_snippet,
                    "partition": partition,
                    "offset": offset,
                },
            )
            self._dlq_producer(
                self._settings.kafka_render_dlq_topic,
                job_id,
                dlq.model_dump(mode="json", by_alias=True),
            )
            INVALID_PAYLOAD.inc()
            log.info(
                "consumer.invalid_json_dlq_sent",
                partition=partition,
                offset=offset,
                key=msg_key,
            )
        except Exception:
            log.exception("consumer.invalid_json_dlq_error", partition=partition, offset=offset)

    # ── Lifecycle ────────────────────────────────────────────────────

    def stop(self) -> None:
        self._running = False
        KAFKA_CONSUMER_RUNNING.set(0)
        if self._state:
            self._state.consumer_running = False
        self._consumer.close()
        log.info("consumer.stopped")
