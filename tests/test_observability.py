"""Tests for the maker8.observability package."""

from __future__ import annotations

import json
import time
from pathlib import Path

from maker8.observability.health import HealthManager
from maker8.observability.helpers import Timer, sanitize_url, timed, truncate_stderr
from maker8.observability.state import WorkerState

# ── Timer tests ──────────────────────────────────────────────────────────────


class TestTimer:
    def test_start_stop_measures_time(self) -> None:
        t = Timer().start()
        time.sleep(0.01)
        t.stop()
        assert t.elapsed_sec >= 0.01
        assert t.elapsed_ms >= 10

    def test_not_started_returns_zero(self) -> None:
        t = Timer()
        assert t.elapsed_sec == 0.0
        assert t.elapsed_ms == 0.0

    def test_start_returns_self(self) -> None:
        t = Timer()
        result = t.start()
        assert result is t

    def test_stop_returns_self(self) -> None:
        t = Timer().start()
        result = t.stop()
        assert result is t


class TestTimed:
    def test_timed_context_manager(self) -> None:
        with timed() as t:
            time.sleep(0.01)
        assert t.elapsed_sec >= 0.01


# ── sanitize_url tests ──────────────────────────────────────────────────────


class TestSanitizeUrl:
    def test_strips_query_params(self) -> None:
        result = sanitize_url("https://example.com/path?key=secret&token=abc")
        assert "secret" not in result
        assert "abc" not in result
        assert "example.com/path" in result

    def test_truncates_long_url(self) -> None:
        long_url = "https://example.com/" + "a" * 300
        result = sanitize_url(long_url, max_len=50)
        assert len(result) <= 53  # 50 + "..."
        assert result.endswith("...")

    def test_preserves_short_url(self) -> None:
        url = "https://example.com/video.mp4"
        result = sanitize_url(url)
        assert result == "https://example.com/video.mp4"

    def test_handles_empty_url(self) -> None:
        assert sanitize_url("") == ""


# ── truncate_stderr tests ────────────────────────────────────────────────────


class TestTruncateStderr:
    def test_short_string_unchanged(self) -> None:
        assert truncate_stderr("short error") == "short error"

    def test_long_string_truncated(self) -> None:
        long = "x" * 1000
        result = truncate_stderr(long, max_len=100)
        assert len(result) <= 103  # "..." prefix + 100 chars
        assert result.startswith("...")

    def test_empty_string_returns_sentinel(self) -> None:
        assert truncate_stderr("") == "(no stderr)"

    def test_none_returns_sentinel(self) -> None:
        assert truncate_stderr(None) == "(no stderr)"


# ── WorkerState tests ────────────────────────────────────────────────────────


class TestWorkerState:
    def test_initial_state(self) -> None:
        state = WorkerState()
        assert state.current_job_id is None
        assert state.consumer_running is False
        assert state.process_started_at > 0

    def test_on_message_received(self) -> None:
        state = WorkerState()
        state.on_message_received(partition=0, offset=42)
        assert state.last_kafka_partition == 0
        assert state.last_kafka_offset == 42

    def test_on_job_started(self) -> None:
        state = WorkerState()
        state.on_job_started("test-job-123", "test-key")
        assert state.current_job_id == "test-job-123"
        assert state.current_job_key == "test-key"

    def test_on_stage_enter(self) -> None:
        state = WorkerState()
        state.on_job_started("j1", "k1")
        state.on_stage_enter("VALIDATE", attempt=1)
        assert state.current_stage == "VALIDATE"
        assert state.current_attempt == 1

    def test_on_job_success_clears_current(self) -> None:
        state = WorkerState()
        state.on_job_started("j1", "k1")
        state.on_job_success("j1")
        assert state.current_job_id is None
        assert state.last_success_job_id == "j1"

    def test_on_job_failure_clears_current(self) -> None:
        state = WorkerState()
        state.on_job_started("j1", "k1")
        state.on_job_failure("j1", "RENDER", "RENDER_FAILED")
        assert state.current_job_id is None
        assert state.last_failure_job_id == "j1"
        assert state.last_failure_stage == "RENDER"

    def test_snapshot_returns_dict(self) -> None:
        state = WorkerState()
        snap = state.snapshot()
        assert isinstance(snap, dict)
        assert "started_at_epoch" in snap
        assert "consumer_running" in snap
        assert snap["service"] == "maker8"

    def test_flush_writes_json(self, tmp_path: Path) -> None:
        state = WorkerState()
        state.on_job_started("j-flush", "k-flush")
        status_file = tmp_path / "status.json"
        state.flush(status_file)
        assert status_file.exists()
        data = json.loads(status_file.read_text())
        assert data["current_job"]["job_id"] == "j-flush"


# ── HealthManager tests ─────────────────────────────────────────────────────


class TestHealthManager:
    def test_mark_live_creates_file(self, tmp_path: Path) -> None:
        state = WorkerState()
        hm = HealthManager(
            state=state,
            live_path=tmp_path / "live",
            ready_path=tmp_path / "ready",
            status_path=tmp_path / "status.json",
        )
        hm.mark_live()
        assert (tmp_path / "live").exists()

    def test_mark_not_live_removes_file(self, tmp_path: Path) -> None:
        state = WorkerState()
        hm = HealthManager(
            state=state,
            live_path=tmp_path / "live",
            ready_path=tmp_path / "ready",
            status_path=tmp_path / "status.json",
        )
        hm.mark_live()
        hm.mark_not_live()
        assert not (tmp_path / "live").exists()

    def test_mark_ready_creates_file(self, tmp_path: Path) -> None:
        state = WorkerState()
        hm = HealthManager(
            state=state,
            live_path=tmp_path / "live",
            ready_path=tmp_path / "ready",
            status_path=tmp_path / "status.json",
        )
        hm.mark_ready()
        assert (tmp_path / "ready").exists()

    def test_flush_status_writes_json(self, tmp_path: Path) -> None:
        state = WorkerState()
        state.on_job_started("j-health", "k-health")
        hm = HealthManager(
            state=state,
            live_path=tmp_path / "live",
            ready_path=tmp_path / "ready",
            status_path=tmp_path / "status.json",
        )
        hm.flush_status()
        data = json.loads((tmp_path / "status.json").read_text())
        assert data["current_job"]["job_id"] == "j-health"

    def test_cleanup_removes_all(self, tmp_path: Path) -> None:
        state = WorkerState()
        hm = HealthManager(
            state=state,
            live_path=tmp_path / "live",
            ready_path=tmp_path / "ready",
            status_path=tmp_path / "status.json",
        )
        hm.mark_live()
        hm.mark_ready()
        hm.flush_status()
        hm.cleanup()
        assert not (tmp_path / "live").exists()
        assert not (tmp_path / "ready").exists()
        assert not (tmp_path / "status.json").exists()


# ── DLQPayload enrichment test ──────────────────────────────────────────────


class TestDLQPayloadEnrichment:
    def test_dlq_has_new_fields(self) -> None:
        from maker8.models.contracts import DLQPayload

        dlq = DLQPayload(
            job_id="j1",
            failed_stage="RENDER",
            attempts=3,
            max_attempts=5,
            debug_context={"resolved_asset_ids": ["a1", "a2"]},
        )
        assert dlq.max_attempts == 5
        assert dlq.debug_context["resolved_asset_ids"] == ["a1", "a2"]

    def test_dlq_defaults_backward_compatible(self) -> None:
        from maker8.models.contracts import DLQPayload

        dlq = DLQPayload(job_id="j1", failed_stage="TTS", attempts=1)
        assert dlq.max_attempts == 0
        assert dlq.debug_context == {}
