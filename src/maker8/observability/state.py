"""Centralized worker runtime state – in-memory + JSON file flush."""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "WorkerState",
]

_VERSION = "0.1.0"


@dataclass
class WorkerState:
    """Mutable singleton tracking what the worker is doing right now.

    Updated at key points:
    - app startup / shutdown
    - consumer receives a message
    - orchestrator enters/exits a stage
    - retry sleep scheduled
    - job completes (success/failure)
    """

    process_started_at: float = field(default_factory=time.time)
    consumer_running: bool = False
    _on_change: Callable[[], None] | None = field(default=None, repr=False)

    # Current job
    current_job_id: str | None = None
    current_job_key: str | None = None
    current_stage: str | None = None
    current_attempt: int = 0
    stage_started_at: float | None = None

    # Retry
    retry_sleep_until: float | None = None

    # Last success
    last_success_at: float | None = None
    last_success_job_id: str | None = None

    # Last failure
    last_failure_at: float | None = None
    last_failure_code: str | None = None
    last_failure_stage: str | None = None
    last_failure_job_id: str | None = None

    # Kafka provenance
    last_kafka_partition: int | None = None
    last_kafka_offset: int | None = None

    def set_on_change(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked after every state mutation."""
        self._on_change = callback

    def _notify(self) -> None:
        if self._on_change:
            with contextlib.suppress(Exception):
                self._on_change()

    # ── Lifecycle helpers ────────────────────────────────────────────

    def on_message_received(
        self,
        *,
        partition: int | None = None,
        offset: int | None = None,
    ) -> None:
        self.last_kafka_partition = partition
        self.last_kafka_offset = offset
        self._notify()

    def on_job_started(self, job_id: str, job_key: str = "") -> None:
        self.current_job_id = job_id
        self.current_job_key = job_key
        self.current_stage = None
        self.current_attempt = 0
        self.stage_started_at = None
        self.retry_sleep_until = None
        self._notify()

    def on_stage_enter(self, stage: str, attempt: int = 1) -> None:
        self.current_stage = stage
        self.current_attempt = attempt
        self.stage_started_at = time.time()
        self._notify()

    def on_retry_sleep(self, delay_sec: float) -> None:
        self.retry_sleep_until = time.time() + delay_sec
        self._notify()

    def on_job_success(self, job_id: str) -> None:
        self.last_success_at = time.time()
        self.last_success_job_id = job_id
        self._clear_current()
        self._notify()

    def on_job_failure(self, job_id: str, stage: str, code: str) -> None:
        self.last_failure_at = time.time()
        self.last_failure_code = code
        self.last_failure_stage = stage
        self.last_failure_job_id = job_id
        self._clear_current()
        self._notify()

    def _clear_current(self) -> None:
        self.current_job_id = None
        self.current_job_key = None
        self.current_stage = None
        self.current_attempt = 0
        self.stage_started_at = None
        self.retry_sleep_until = None

    # ── Snapshot ─────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable dict suitable for status file."""
        now = time.time()
        current_job: dict[str, Any] | None = None
        if self.current_job_id:
            elapsed = round(now - self.stage_started_at, 1) if self.stage_started_at else None
            current_job = {
                "job_id": self.current_job_id,
                "job_key": self.current_job_key,
                "stage": self.current_stage,
                "attempt": self.current_attempt,
                "elapsed_sec": elapsed,
            }

        retry: dict[str, Any] | None = None
        if self.retry_sleep_until and self.retry_sleep_until > now:
            retry = {
                "sleep_until_epoch": round(self.retry_sleep_until, 1),
                "remaining_sec": round(self.retry_sleep_until - now, 1),
            }

        last_success: dict[str, Any] | None = None
        if self.last_success_at:
            last_success = {
                "job_id": self.last_success_job_id,
                "at_epoch": round(self.last_success_at, 1),
            }

        last_failure: dict[str, Any] | None = None
        if self.last_failure_at:
            last_failure = {
                "job_id": self.last_failure_job_id,
                "stage": self.last_failure_stage,
                "code": self.last_failure_code,
                "at_epoch": round(self.last_failure_at, 1),
            }

        return {
            "service": "maker8",
            "version": _VERSION,
            "started_at_epoch": round(self.process_started_at, 1),
            "consumer_running": self.consumer_running,
            "current_job": current_job,
            "retry": retry,
            "last_success": last_success,
            "last_failure": last_failure,
        }

    def flush(self, path: Path) -> None:
        """Write snapshot JSON to *path* atomically (write-rename)."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.snapshot(), indent=2), encoding="utf-8")
        tmp.rename(path)
