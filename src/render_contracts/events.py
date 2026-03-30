"""Kafka topic constants.

Shared between editor8 and maker8 for wire-format compatibility.
Wire-format models live in ``render_contracts.render_spec`` – import from there.
"""

from __future__ import annotations

# ── Topic Constants ──────────────────────────────────────────────────────────

RENDER_REQUEST_TOPIC = "video.render.request.v1"
RENDER_RESULT_TOPIC = "video.render.result.v1"
EDITOR_INPUT_TOPIC = "editor8.input.v1"
EDITOR_DLQ_TOPIC = "editor8.dlq.v1"
RENDER_DLQ_TOPIC = "video.render.dlq.v1"
