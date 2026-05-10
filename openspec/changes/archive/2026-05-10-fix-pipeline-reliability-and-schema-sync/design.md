## Context

Maker8 is a single-threaded synchronous Kafka consumer that processes one render job at a time through a fixed stage pipeline (VALIDATE → RESOLVE_ASSETS → DOWNLOAD → NORMALIZE → TTS → RENDER → UPLOAD_DROPBOX → EMIT_RESULT). A systematic audit found four correctness defects and one schema-drift problem that need to be fixed before the service can be considered production-reliable.

Current pain points:
- Transient failures (e.g. `OSError`, network timeouts) that should be retried are silently treated as permanent failures because the retry exception-routing logic is broken.
- A job with an invalid `job_id` or malformed payload causes the consumer to silently commit the Kafka offset, permanently losing the message with no trace.
- Double-SIGINT kills the process with `os._exit(0)`, bypassing `atexit` and leaving the health-status file stale.
- Published JSON schemas in `docs/schemas/` have drifted from the Pydantic source models, breaking downstream tooling.

## Goals / Non-Goals

**Goals:**
- Correct the `StageError` retry-routing bug so transient stage exceptions are retried up to `render_max_attempts` times.
- Enforce `render_max_attempts >= 1` at startup via Pydantic validation so zero/negative values are rejected before any message is consumed.
- Emit unhandleable messages to a configurable dead-letter Kafka topic with error metadata before committing the source offset.
- Replace `os._exit(0)` with a coordinated shutdown path that guarantees cleanup hooks execute.
- Regenerate JSON schemas from Pydantic models and close the `render_contracts` export gap.
- Add the eight missing identity assertions to `TestModelIdentity`.

**Non-Goals:**
- Converting the worker to async/concurrent processing.
- Changing the stage pipeline ordering or introducing new stages.
- Altering Kafka consumer group or partition assignment strategy.
- Adding new wire-model fields or breaking contract changes.

## Decisions

### Decision 1: Fix retry routing by unifying exception handling in a single `except` clause

**Problem**: The current `_execute_with_retry` has sibling `except StageError` and `except Exception` blocks in the same `try` statement. When `except Exception` catches an `OSError` and raises a new `StageError`, Python cannot re-route that raise to the sibling `except StageError` — it propagates out of the try block entirely, bypassing retry logic.

**Decision**: Collapse to a single `except Exception` clause that classifies the exception inline:

```python
except Exception as exc:
    err = exc if isinstance(exc, StageError) else StageError(str(exc), retryable=True) from exc
    if err.retryable and attempt < max_attempts - 1:
        # sleep and continue
    raise err
```

**Alternative considered**: Nested try/except (inner for stage call, outer for retry). Rejected — more indentation, same semantics, and harder to read for a synchronous loop.

---

### Decision 2: Validate `render_max_attempts` with Pydantic `Field(ge=1)`

**Decision**: Add `Field(ge=1, default=3)` to `Settings.render_max_attempts`. Pydantic raises `ValidationError` at startup if the env var is `0` or negative. This is a one-line change and provides a clear error message without any custom validator code.

---

### Decision 3: DLQ via existing `KafkaProducer`, not a separate client

**Decision**: Reuse the existing `KafkaProducer` instance to emit DLQ messages. The DLQ topic name is a new optional config setting `MAKER8_DLQ_TOPIC` (default `"maker8.dead-letter"`). The DLQ message payload is:

```json
{
  "source_topic": "<original topic>",
  "source_partition": 0,
  "source_offset": 0,
  "error_type": "ValueError",
  "error_message": "...",
  "raw_payload": "<base64-encoded original bytes>",
  "timestamp_utc": "..."
}
```

The consumer emits to the DLQ **before** committing the source offset. If the DLQ emit itself fails, the source offset is not committed and the message is retried (at-least-once delivery guarantee preserved).

**Alternative considered**: A dedicated DLQ sink outside Kafka (e.g., S3, database). Rejected — adds an external dependency; a DLQ Kafka topic is standard and consistent with the existing infrastructure.

---

### Decision 4: Coordinated shutdown via `threading.Event`

**Decision**: Replace `os._exit(0)` in the double-SIGINT handler with `shutdown_event.set()`. The main consumer loop already checks for a stop condition; setting the event causes it to exit its poll loop cleanly, allowing `HealthManager.cleanup()`, the updater thread join, and `atexit` handlers to run before process exit.

The first SIGINT still initiates graceful shutdown (existing behavior). Only the second SIGINT path is changed (from `os._exit(0)` to `shutdown_event.set()` with a short hard-kill timeout — if cleanup exceeds 5 seconds, a fallback `sys.exit(1)` is used instead of `os._exit`).

**Alternative considered**: Keep `os._exit(0)` and move cleanup into the signal handler. Rejected — signal handlers run on arbitrary threads and must not call non-reentrant code; moving cleanup there risks deadlocks.

---

### Decision 5: Deferred Kafka flush — flush on clean shutdown and periodic interval

**Decision**: Remove `flush()` from `KafkaProducer.send()`. Add a `flush()` call in:
1. The producer's `close()` / `__exit__` method (already ensures delivery before shutdown).
2. A periodic flush triggered from the consumer loop (every N messages or N seconds, configurable via `MAKER8_PRODUCER_FLUSH_INTERVAL`, default 30 seconds).

This keeps delivery guarantees while removing the per-message blocking round-trip.

---

### Decision 6: Schema regeneration via Pydantic `.model_json_schema()`

**Decision**: Add a lightweight `scripts/generate_schemas.py` that calls `model_json_schema()` on `RenderRequest` and `RenderResult` and writes the output to `docs/schemas/`. This script becomes the single source of truth for schema generation and is documented in `AGENTS.md`. The `render_contracts/__init__.py` is updated to re-export the four primary types (`RenderRequest`, `RenderResult`, `RenderSpec`, `RenderSpecV2`).

## Risks / Trade-offs

- **DLQ emit failure on a broken broker** → If the broker is down, DLQ emit will fail; the source offset won't be committed and the message will be re-delivered. In sustained broker outages this can cause the consumer to stall. Mitigation: the existing health probe will report unhealthy on prolonged poll intervals; ops runbook already documents this path.
- **Unified `except Exception` makes all unknown exceptions retryable** → An unexpected bug in a stage (e.g., `AttributeError`) will now be retried up to `render_max_attempts` times, delaying failure reporting. Mitigation: keep `render_max_attempts` small (default 3); retryable flag on `StageError` still controls per-stage override.
- **Schema regeneration may change key ordering** → JSON schema `properties` ordering is deterministic from Pydantic field definition order, but downstream tools that depend on exact byte-for-byte schema content will need to refresh their cached schemas. Mitigation: communicate schema version bump via changelog.

## Migration Plan

1. Deploy config change (`MAKER8_DLQ_TOPIC`, `MAKER8_PRODUCER_FLUSH_INTERVAL`) — backward compatible; defaults provided.
2. Create the DLQ Kafka topic on the broker before deploying (or tolerate topic-auto-create if enabled).
3. Deploy the code changes. No migration of existing messages required.
4. Regenerate and commit updated `docs/schemas/*.json`.
5. Rollback: revert to prior image tag; DLQ topic can remain unused.

## Open Questions

- Should non-retryable `StageError` instances still be wrapped with metadata before being emitted to the DLQ, or only messages that fail before the pipeline begins?
- Is there a preferred flush interval for the Kafka producer, or should it track message count instead of wall-clock time?
