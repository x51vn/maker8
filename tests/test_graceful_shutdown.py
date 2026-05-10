"""Tests for graceful-shutdown signal handling in app.py (task group 3).

Verifies that a simulated double-SIGINT:
  1. Sets ``_shutdown_event`` instead of calling ``os._exit``.
  2. Starts a 5-second daemon timer as a hard-timeout fallback.
  3. Does NOT call ``os._exit`` directly.

Also verifies that the consumer poll loop breaks immediately when
``stop_event`` is set before (or during) the loop.
"""

from __future__ import annotations

import signal
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import maker8.app as app_module
from maker8.kafka.consumer import RenderConsumer


# ── Helpers ──────────────────────────────────────────────────────────────────


def _reset_app_state() -> None:
    """Reset module-level shutdown globals between tests."""
    app_module._shutdown_requested = False
    app_module._shutdown_event.clear()
    app_module._consumer = None
    app_module._health = None
    app_module._log = None


def _make_settings_mock() -> MagicMock:
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


# ── Tests: double-SIGINT path ─────────────────────────────────────────────────


class TestDoubleSignalShutdown:
    def setup_method(self) -> None:
        _reset_app_state()

    def teardown_method(self) -> None:
        _reset_app_state()

    def test_first_sigint_sets_shutdown_requested(self) -> None:
        """First SIGINT: sets _shutdown_requested and calls consumer.stop()."""
        mock_consumer = MagicMock()
        app_module._consumer = mock_consumer

        with patch.object(app_module, "WORKER_READY"):
            app_module._shutdown(signal.SIGINT, None)

        assert app_module._shutdown_requested is True
        mock_consumer.stop.assert_called_once()

    def test_double_sigint_does_not_call_os_exit(self) -> None:
        """Double-SIGINT must NOT call os._exit; cleanup path must remain open."""
        app_module._shutdown_requested = True  # simulate already-shutting-down

        with patch("maker8.app.os._exit") as mock_os_exit:
            # Timer would fire after 5s; cancel it to avoid real wait
            timers: list[threading.Timer] = []
            original_timer = threading.Timer

            def _capture_timer(interval: float, func: Any, *a: Any, **kw: Any) -> threading.Timer:
                t = original_timer(interval, func, *a, **kw)
                timers.append(t)
                return t

            with patch("maker8.app.threading.Timer", side_effect=_capture_timer):
                app_module._shutdown(signal.SIGINT, None)

        for t in timers:
            t.cancel()

        mock_os_exit.assert_not_called()

    def test_double_sigint_sets_shutdown_event(self) -> None:
        """Double-SIGINT must set _shutdown_event."""
        app_module._shutdown_requested = True

        timers: list[threading.Timer] = []
        original_timer = threading.Timer

        def _capture_timer(interval: float, func: Any, *a: Any, **kw: Any) -> threading.Timer:
            t = original_timer(interval, func, *a, **kw)
            timers.append(t)
            return t

        with patch("maker8.app.threading.Timer", side_effect=_capture_timer):
            app_module._shutdown(signal.SIGINT, None)

        for t in timers:
            t.cancel()

        assert app_module._shutdown_event.is_set()

    def test_double_sigint_starts_5s_daemon_timer(self) -> None:
        """Double-SIGINT must start a 5-second daemon timer as a hard-timeout."""
        app_module._shutdown_requested = True

        timers: list[threading.Timer] = []
        original_timer = threading.Timer

        def _capture_timer(interval: float, func: Any, *a: Any, **kw: Any) -> threading.Timer:
            t = original_timer(interval, func, *a, **kw)
            timers.append(t)
            return t

        with patch("maker8.app.threading.Timer", side_effect=_capture_timer):
            app_module._shutdown(signal.SIGINT, None)

        assert len(timers) == 1
        assert timers[0].interval == 5.0
        assert timers[0].daemon is True
        timers[0].cancel()  # prevent real timeout

    def test_first_sigint_does_not_start_timer(self) -> None:
        """The first SIGINT must NOT start a hard-timeout timer."""
        app_module._consumer = MagicMock()

        timers: list[threading.Timer] = []
        original_timer = threading.Timer

        def _capture_timer(interval: float, func: Any, *a: Any, **kw: Any) -> threading.Timer:
            t = original_timer(interval, func, *a, **kw)
            timers.append(t)
            return t

        with patch("maker8.app.threading.Timer", side_effect=_capture_timer):
            with patch.object(app_module, "WORKER_READY"):
                app_module._shutdown(signal.SIGINT, None)

        assert len(timers) == 0


# ── Tests: consumer stop_event integration ───────────────────────────────────


class TestConsumerStopEvent:
    def test_stop_event_already_set_skips_poll(self) -> None:
        """When stop_event is already set, start() breaks before calling poll."""
        stop_event = threading.Event()
        stop_event.set()

        with patch("maker8.kafka.consumer.Consumer") as mock_kafka_cls:
            mock_confluent = MagicMock()
            mock_kafka_cls.return_value = mock_confluent

            consumer = RenderConsumer(_make_settings_mock())
            consumer.start(handler=lambda _: None, stop_event=stop_event)

        mock_confluent.poll.assert_not_called()

    def test_stop_event_set_during_poll_loop_breaks(self) -> None:
        """Consumer loop breaks as soon as stop_event is set (within poll cycle)."""
        stop_event = threading.Event()
        call_count = {"n": 0}

        def _poll_side_effect(timeout: float) -> Any:
            call_count["n"] += 1
            # Set the event on first poll — loop should break next iteration
            stop_event.set()
            return None  # no message

        with patch("maker8.kafka.consumer.Consumer") as mock_kafka_cls:
            mock_confluent = MagicMock()
            mock_kafka_cls.return_value = mock_confluent
            mock_confluent.poll.side_effect = _poll_side_effect

            consumer = RenderConsumer(_make_settings_mock())
            consumer.start(handler=lambda _: None, stop_event=stop_event)

        # poll() was called once; loop broke on second iteration check
        assert call_count["n"] == 1

    def test_no_stop_event_uses_running_flag(self) -> None:
        """Without stop_event, the loop still exits via consumer.stop()."""
        with patch("maker8.kafka.consumer.Consumer") as mock_kafka_cls:
            mock_confluent = MagicMock()
            mock_kafka_cls.return_value = mock_confluent

            stop_sentinel = MagicMock()
            stop_sentinel.error.return_value = None
            stop_sentinel.value.return_value = b'{"__stop": true}'
            stop_sentinel.key.return_value = b"stop"
            stop_sentinel.partition.return_value = 0
            stop_sentinel.offset.return_value = 0
            mock_confluent.poll.side_effect = [stop_sentinel] + [None] * 5

            consumer = RenderConsumer(_make_settings_mock())

            def _handler(payload: dict[str, Any]) -> None:
                if payload.get("__stop"):
                    consumer.stop()

            # stop_event=None — uses _running flag only
            consumer.start(handler=_handler, stop_event=None)

        assert consumer._running is False
