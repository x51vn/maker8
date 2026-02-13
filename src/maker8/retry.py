"""Retry policy, back-off computation, and StageError.

All retry decisions in the Render Worker are made through ``RetryPolicy``
and the ``RENDER_RETRYABLE_STAGES`` set.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from maker8.models.common import RenderStage

# ── Retryable-stage set (single source of truth) ────────────────────────────

RENDER_RETRYABLE_STAGES: frozenset[RenderStage] = frozenset(
    {
        RenderStage.RESOLVE_ASSETS,
        RenderStage.DOWNLOAD,
        RenderStage.TTS,
        RenderStage.UPLOAD_DROPBOX,
        RenderStage.EMIT_RESULT,
    }
)


# ── Retry policy ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential back-off configuration for pipeline stages.

    Defaults match *Spec §7.1*:
      max_attempts = 5, 1 min → 6 h backoff.
    """

    max_attempts: int = 5
    min_delay_sec: float = 60.0
    max_delay_sec: float = 21600.0  # 6 hours
    jitter_factor: float = 0.1
    retryable_stages: frozenset[RenderStage] = field(
        default_factory=lambda: RENDER_RETRYABLE_STAGES
    )

    def is_retryable(self, stage: RenderStage) -> bool:
        return stage in self.retryable_stages

    def delay(self, attempt: int) -> float:
        """Compute back-off delay in seconds for a 1-based *attempt*."""
        base = min(self.min_delay_sec * (2 ** (attempt - 1)), self.max_delay_sec)
        jitter = base * self.jitter_factor * random.random()
        return float(base + jitter)

    def sleep(self, attempt: int) -> None:  # pragma: no cover
        """Block the current thread for the computed back-off delay."""
        time.sleep(self.delay(attempt))


# ── StageError ───────────────────────────────────────────────────────────────


class StageError(Exception):
    """Raised by a pipeline stage to signal failure.

    ``retryable`` defaults to ``True`` if the stage is in
    ``RENDER_RETRYABLE_STAGES``; callers may force it.
    """

    def __init__(
        self,
        stage: RenderStage,
        code: str,
        message: str,
        retryable: bool | None = None,
    ) -> None:
        self.stage = stage
        self.code = code
        self.retryable = (
            retryable if retryable is not None else (stage in RENDER_RETRYABLE_STAGES)
        )
        super().__init__(message)
