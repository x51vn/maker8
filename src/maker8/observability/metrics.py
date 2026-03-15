"""Prometheus metrics for the Maker8 Render Worker.

Metrics are defined once at module level and updated by instrumentation
call-sites throughout the codebase.  The HTTP server is started (if
enabled) by ``app.py`` via ``start_metrics_server()``.

Cardinality rules:
- Allowed labels: ``stage``, ``error_code``, ``source_kind``, ``provider``,
  ``status``, ``dependency``.
- Never use ``job_id``, ``asset_id``, full URLs, or raw exception strings
  as label values.
"""

from __future__ import annotations

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
)

__all__ = [
    "CURRENT_STAGE",
    "DEPENDENCY_FAILURES",
    "DLQ_EMITTED",
    "DOWNLOAD_BYTES",
    "INVALID_PAYLOAD",
    "JOBS_FAILED",
    "JOBS_RECEIVED",
    "JOBS_SUCCEEDED",
    "JOB_DURATION",
    "JOB_IN_PROGRESS",
    "KAFKA_CONSUMER_RUNNING",
    "LAST_FAILURE_UNIXTIME",
    "LAST_SUCCESS_UNIXTIME",
    "RESULT_EMITTED",
    "RETRIES_SCHEDULED",
    "RETRY_SLEEP_SECONDS",
    "STAGE_DURATION",
    "SUBPROCESS_DURATION",
    "SUBPROCESS_FAILURES",
    "TTS_DURATION",
    "WORKER_READY",
    "WORKER_UP",
]


# ── Counters ─────────────────────────────────────────────────────────────────

JOBS_RECEIVED = Counter(
    "maker8_jobs_received_total",
    "Total Kafka messages received (valid or not).",
)

JOBS_SUCCEEDED = Counter(
    "maker8_jobs_succeeded_total",
    "Jobs that completed the full pipeline successfully.",
)

JOBS_FAILED = Counter(
    "maker8_jobs_failed_total",
    "Jobs that failed permanently.",
    ["stage", "error_code"],
)

INVALID_PAYLOAD = Counter(
    "maker8_invalid_payload_total",
    "Messages that could not be parsed as valid RenderRequest.",
)

DLQ_EMITTED = Counter(
    "maker8_dlq_emitted_total",
    "DLQ messages produced.",
    ["stage"],
)

RESULT_EMITTED = Counter(
    "maker8_result_emitted_total",
    "Render result messages produced.",
    ["status"],
)

RETRIES_SCHEDULED = Counter(
    "maker8_retries_scheduled_total",
    "Stage retries scheduled.",
    ["stage"],
)

SUBPROCESS_FAILURES = Counter(
    "maker8_subprocess_failures_total",
    "External process failures (yt-dlp, ffmpeg, ffprobe).",
    ["stage", "source_kind"],
)

DEPENDENCY_FAILURES = Counter(
    "maker8_dependency_failures_total",
    "External dependency failures.",
    ["dependency"],
)


# ── Histograms ───────────────────────────────────────────────────────────────

JOB_DURATION = Histogram(
    "maker8_job_duration_seconds",
    "Wall-clock duration of a full pipeline run.",
    ["status"],
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)

STAGE_DURATION = Histogram(
    "maker8_stage_duration_seconds",
    "Duration of individual pipeline stages.",
    ["stage", "status"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
)

SUBPROCESS_DURATION = Histogram(
    "maker8_subprocess_duration_seconds",
    "Duration of external subprocess calls.",
    ["stage", "source_kind"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
)

TTS_DURATION = Histogram(
    "maker8_tts_duration_seconds",
    "TTS synthesis duration per scene.",
    ["provider"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
)

DOWNLOAD_BYTES = Histogram(
    "maker8_download_bytes",
    "Bytes downloaded per asset.",
    ["source_kind"],
    buckets=(1e4, 1e5, 1e6, 5e6, 1e7, 5e7, 1e8, 5e8, 1e9),
)


# ── Gauges ───────────────────────────────────────────────────────────────────

WORKER_UP = Gauge(
    "maker8_worker_up",
    "1 if the worker process is alive.",
)

WORKER_READY = Gauge(
    "maker8_worker_ready",
    "1 if the worker has completed bootstrap and is consuming.",
)

JOB_IN_PROGRESS = Gauge(
    "maker8_job_in_progress",
    "1 if a job is currently being processed.",
)

CURRENT_STAGE = Gauge(
    "maker8_current_stage",
    "Ordinal of the current pipeline stage (0 = idle).",
)

RETRY_SLEEP_SECONDS = Gauge(
    "maker8_retry_sleep_seconds",
    "Seconds remaining in the current retry sleep (0 if not sleeping).",
)

LAST_SUCCESS_UNIXTIME = Gauge(
    "maker8_last_success_unixtime",
    "Unix timestamp of the last successful job completion.",
)

LAST_FAILURE_UNIXTIME = Gauge(
    "maker8_last_failure_unixtime",
    "Unix timestamp of the last job failure.",
)

KAFKA_CONSUMER_RUNNING = Gauge(
    "maker8_kafka_consumer_running",
    "1 if the Kafka consumer loop is active.",
)

# Stage ordinal mapping for CURRENT_STAGE gauge
_STAGE_ORDINALS: dict[str | None, int] = {
    None: 0,
    "VALIDATE": 1,
    "RESOLVE_ASSETS": 2,
    "DOWNLOAD": 3,
    "NORMALIZE": 4,
    "TTS": 5,
    "RENDER": 6,
    "UPLOAD_DROPBOX": 7,
    "EMIT_RESULT": 8,
}


def set_current_stage(stage: str | None) -> None:
    """Update the ``CURRENT_STAGE`` gauge using the ordinal map."""
    CURRENT_STAGE.set(_STAGE_ORDINALS.get(stage, 0))
