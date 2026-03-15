"""Kafka event envelope models and topic constants.

Shared between editor8 and maker8 for wire-format compatibility.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from render_contracts.render_spec import RenderSpec, ResultDestination, Trace

# ── Topic Constants ──────────────────────────────────────────────────────────

RENDER_REQUEST_TOPIC = "video.render.request.v1"
RENDER_RESULT_TOPIC = "video.render.result.v1"
EDITOR_INPUT_TOPIC = "editor8.input.v1"
EDITOR_DLQ_TOPIC = "editor8.dlq.v1"
RENDER_DLQ_TOPIC = "video.render.dlq.v1"


# ── RenderRequest ────────────────────────────────────────────────────────────


class RenderRequest(BaseModel):
    """``video.render.request.v1`` – the output payload published to Kafka."""

    job_id: str
    spec_version: str = "1.0"
    render_spec: RenderSpec
    result: ResultDestination = Field(default_factory=ResultDestination)
    trace: Trace = Field(default_factory=Trace)
