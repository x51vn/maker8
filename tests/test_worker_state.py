"""Tests for XST-1055: WorkerState removes never-populated asset/scene fields."""

from __future__ import annotations

from maker8.observability.state import WorkerState


class TestWorkerStateSnapshotNoAssetSceneFields:
    def test_snapshot_no_asset_id_key(self) -> None:
        """snapshot current_job dict must not contain asset_id."""
        state = WorkerState()
        state.on_job_started("job-x", "key-1")
        state.on_stage_enter("RENDER", attempt=1)
        snap = state.snapshot()
        cj = snap.get("current_job")
        assert cj is not None
        assert "asset_id" not in cj

    def test_snapshot_no_scene_id_key(self) -> None:
        """snapshot current_job dict must not contain scene_id."""
        state = WorkerState()
        state.on_job_started("job-x", "key-1")
        state.on_stage_enter("RENDER", attempt=1)
        snap = state.snapshot()
        cj = snap.get("current_job")
        assert cj is not None
        assert "scene_id" not in cj


class TestWorkerStateExistingFieldsUnaffected:
    def test_current_stage_updated(self) -> None:
        state = WorkerState()
        state.on_job_started("j1", "k1")
        state.on_stage_enter("DOWNLOAD", attempt=2)
        assert state.current_stage == "DOWNLOAD"
        assert state.current_attempt == 2

    def test_current_job_id_updated(self) -> None:
        state = WorkerState()
        state.on_job_started("j2", "k2")
        assert state.current_job_id == "j2"
        assert state.current_job_key == "k2"

    def test_cleared_after_success(self) -> None:
        state = WorkerState()
        state.on_job_started("j3", "k3")
        state.on_stage_enter("EMIT_RESULT")
        state.on_job_success("j3")
        assert state.current_job_id is None
        assert state.current_stage is None

    def test_cleared_after_failure(self) -> None:
        state = WorkerState()
        state.on_job_started("j4", "k4")
        state.on_stage_enter("VALIDATE")
        state.on_job_failure("j4", stage="VALIDATE", code="SPEC_INVALID")
        assert state.current_job_id is None
        assert state.last_failure_code == "SPEC_INVALID"

    def test_no_current_asset_id_attribute(self) -> None:
        """WorkerState must not declare current_asset_id at all."""
        state = WorkerState()
        assert not hasattr(state, "current_asset_id")

    def test_no_current_scene_id_attribute(self) -> None:
        """WorkerState must not declare current_scene_id at all."""
        state = WorkerState()
        assert not hasattr(state, "current_scene_id")
