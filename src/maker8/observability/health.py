"""Health semantics – liveness, readiness, and status file management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from maker8.observability.state import WorkerState

__all__ = [
    "HealthManager",
]


class HealthManager:
    """Manage ``/tmp/maker8_live``, ``/tmp/maker8_ready``, ``/tmp/maker8_status.json``.

    - **liveness**: process is running (file exists while process is alive).
    - **readiness**: all bootstrap dependencies initialised.
    - **status.json**: full runtime snapshot refreshed on every state change.
    """

    def __init__(
        self,
        state: WorkerState,
        *,
        live_path: Path = Path("/tmp/maker8_live"),
        ready_path: Path = Path("/tmp/maker8_ready"),
        status_path: Path = Path("/tmp/maker8_status.json"),
    ) -> None:
        self._state = state
        self._live_path = live_path
        self._ready_path = ready_path
        self._status_path = status_path

    # ── Liveness ─────────────────────────────────────────────────────

    def mark_live(self) -> None:
        self._live_path.touch()

    def mark_not_live(self) -> None:
        self._live_path.unlink(missing_ok=True)

    @property
    def is_live(self) -> bool:
        return self._live_path.exists()

    # ── Readiness ────────────────────────────────────────────────────

    def mark_ready(self) -> None:
        self._ready_path.touch()

    def mark_not_ready(self) -> None:
        self._ready_path.unlink(missing_ok=True)

    @property
    def is_ready(self) -> bool:
        return self._ready_path.exists()

    # ── Status snapshot ──────────────────────────────────────────────

    def flush_status(self) -> None:
        """Write ``WorkerState.snapshot()`` to the status JSON file."""
        self._state.flush(self._status_path)

    def read_status(self) -> dict[str, Any]:
        """Read the status file (for tests / operator tooling)."""
        if self._status_path.exists():
            return json.loads(self._status_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        return {}

    # ── Cleanup ──────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Remove all health files on shutdown."""
        self._live_path.unlink(missing_ok=True)
        self._ready_path.unlink(missing_ok=True)
        self._status_path.unlink(missing_ok=True)
