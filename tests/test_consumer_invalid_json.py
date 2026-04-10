"""Tests for RenderConsumer invalid-JSON DLQ routing (XST-1050).

Verifies that messages whose bytes fail JSON parsing are:
  1. Routed to the DLQ via the injected dlq_producer callback.
  2. Still committed so the offset is advanced (no infinite retry loop).
  3. Valid JSON messages continue to call the handler unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

from maker8.kafka.consumer import RenderConsumer
from maker8.models.contracts import DLQPayload

# ── Fixtures / helpers ───────────────────────────────────────────────────────


def _make_settings() -> MagicMock:
    """Return a minimal Settings mock with Kafka defaults."""
    s = MagicMock()
    s.kafka_bootstrap_servers = "localhost:9092"
    s.kafka_group_id = "test-group"
    s.kafka_render_request_topic = "video.render.request.v1"
    s.kafka_render_dlq_topic = "video.render.dlq.v1"
    s.kafka_max_poll_interval_ms = 300_000
    s.kafka_security_protocol = ""
    s.kafka_sasl_mechanism = ""
    s.kafka_username = ""
    s.kafka_password = ""
    return s


def _make_msg(value: bytes, key: bytes | None = None) -> MagicMock:
    """Return a confluent_kafka.Message mock."""
    msg = MagicMock()
    msg.value.return_value = value
    msg.key.return_value = key
    msg.partition.return_value = 0
    msg.offset.return_value = 42
    msg.error.return_value = None
    return msg


def _run_consumer_with_messages(
    messages: list[MagicMock],
    handler: Callable[[dict[str, Any]], None] | None = None,
    dlq_producer: Callable[[str, str, dict[str, Any]], None] | None = None,
) -> RenderConsumer:
    """Create a consumer, feed it *messages*, then stop it."""
    settings = _make_settings()

    # Append a sentinel to stop the polling loop
    sentinel = MagicMock()
    sentinel.error.return_value = None
    sentinel.value.return_value = b'{"__stop": true}'
    sentinel.key.return_value = b"stop"
    sentinel.partition.return_value = 0
    sentinel.offset.return_value = 99

    poll_responses = messages + [sentinel]

    with patch("maker8.kafka.consumer.Consumer") as mock_kafka:
        mock_confluent = MagicMock()
        mock_kafka.return_value = mock_confluent
        mock_confluent.poll.side_effect = poll_responses + [None] * 100

        consumer = RenderConsumer(settings, dlq_producer=dlq_producer)

        stop_calls = 0

        def _handler(payload: dict[str, Any]) -> None:
            nonlocal stop_calls
            if payload.get("__stop"):
                consumer.stop()
                stop_calls += 1
                return
            if handler:
                handler(payload)

        consumer.start(_handler)

    return consumer


# ── Tests ────────────────────────────────────────────────────────────────────


class TestConsumerInvalidJson:
    def test_invalid_json_emits_dlq(self) -> None:
        """Invalid-JSON message must call dlq_producer with correct topic."""
        bad_msg = _make_msg(b"not valid json at all", key=b"job-123")
        captured: list[tuple[str, str, dict[str, Any]]] = []

        def _dlq(topic: str, key: str, payload: dict[str, Any]) -> None:
            captured.append((topic, key, payload))

        _run_consumer_with_messages([bad_msg], dlq_producer=_dlq)

        assert len(captured) == 1
        topic, key, payload = captured[0]
        assert topic == "video.render.dlq.v1"
        assert key == "job-123"
        assert payload["job_id"] == "job-123"
        assert payload["failed_stage"] == "CONSUMER"
        assert payload["last_error"]["code"] == "INVALID_JSON"

    def test_invalid_json_raw_snippet_in_debug_context(self) -> None:
        """DLQ payload debug_context must include raw_payload_snippet."""
        raw = b"GARBAGE DATA BYTES"
        bad_msg = _make_msg(raw, key=b"job-xyz")
        captured: list[tuple[str, str, dict[str, Any]]] = []

        def _capture(t: str, k: str, p: dict[str, Any]) -> None:
            captured.append((t, k, p))

        _run_consumer_with_messages([bad_msg], dlq_producer=_capture)

        _, _, payload = captured[0]
        dc = payload.get("debug_context", {})
        assert "raw_payload_snippet" in dc
        assert "GARBAGE DATA BYTES" in dc["raw_payload_snippet"]

    def test_invalid_json_commit_is_called(self) -> None:
        """Offset must be committed even for invalid-JSON messages."""
        bad_msg = _make_msg(b"{broken", key=b"any-key")

        with patch("maker8.kafka.consumer.Consumer") as mock_kafka:
            mock_confluent = MagicMock()
            mock_kafka.return_value = mock_confluent

            stop_sentinel = _make_msg(b'{"__stop":true}', key=b"stop")
            mock_confluent.poll.side_effect = [bad_msg, stop_sentinel] + [None] * 10

            settings = _make_settings()
            consumer = RenderConsumer(settings, dlq_producer=lambda *_: None)

            def _h(p: dict[str, Any]) -> None:
                if p.get("__stop"):
                    consumer.stop()

            consumer.start(_h)

        # commit should have been called for the bad message and the sentinel
        assert mock_confluent.commit.call_count >= 1
        # Verify the bad message was committed
        committed_msgs = [c.args[0] for c in mock_confluent.commit.call_args_list]
        assert bad_msg in committed_msgs

    def test_invalid_json_without_dlq_producer_still_commits(self) -> None:
        """When no dlq_producer wired, invalid JSON is swallowed but offset committed."""
        bad_msg = _make_msg(b"not json", key=b"job-noproducer")

        with patch("maker8.kafka.consumer.Consumer") as mock_kafka:
            mock_confluent = MagicMock()
            mock_kafka.return_value = mock_confluent
            stop_sentinel = _make_msg(b'{"__stop":true}', key=b"stop")
            mock_confluent.poll.side_effect = [bad_msg, stop_sentinel] + [None] * 10

            settings = _make_settings()
            consumer = RenderConsumer(settings, dlq_producer=None)  # no producer

            def _h(p: dict[str, Any]) -> None:
                if p.get("__stop"):
                    consumer.stop()

            consumer.start(_h)

        assert mock_confluent.commit.call_count >= 1
        committed_msgs = [c.args[0] for c in mock_confluent.commit.call_args_list]
        assert bad_msg in committed_msgs

    def test_valid_json_calls_handler_not_dlq(self) -> None:
        """A well-formed JSON message must call handler and never dlq_producer."""
        good_msg = _make_msg(b'{"job_id": "good-job"}', key=b"good-job")
        handled: list[dict[str, Any]] = []
        dlq_calls: list[Any] = []

        def _h(p: dict[str, Any]) -> None:
            if not p.get("__stop"):
                handled.append(p)

        _run_consumer_with_messages(
            [good_msg],
            handler=_h,
            dlq_producer=lambda *a: dlq_calls.append(a),
        )

        assert len(handled) == 1
        assert handled[0]["job_id"] == "good-job"
        assert len(dlq_calls) == 0

    def test_invalid_json_key_missing_uses_unknown(self) -> None:
        """When message has no key, DLQ job_id falls back to 'unknown'."""
        bad_msg = _make_msg(b"totally broken", key=None)
        captured: list[tuple[str, str, dict[str, Any]]] = []

        def _capture(t: str, k: str, p: dict[str, Any]) -> None:
            captured.append((t, k, p))

        _run_consumer_with_messages([bad_msg], dlq_producer=_capture)

        assert len(captured) == 1
        _, key, payload = captured[0]
        assert key == "unknown"
        assert payload["job_id"] == "unknown"

    def test_invalid_json_dlq_payload_is_valid_model(self) -> None:
        """DLQ dict emitted must be parseable back into DLQPayload."""
        bad_msg = _make_msg(b"[not an object", key=b"job-model")
        captured: list[tuple[str, str, dict[str, Any]]] = []

        def _capture(t: str, k: str, p: dict[str, Any]) -> None:
            captured.append((t, k, p))

        _run_consumer_with_messages([bad_msg], dlq_producer=_capture)

        assert len(captured) == 1
        _, _, payload = captured[0]
        # Must round-trip through DLQPayload without validation errors
        parsed = DLQPayload.model_validate(payload)
        assert parsed.job_id == "job-model"
        assert parsed.failed_stage == "CONSUMER"
