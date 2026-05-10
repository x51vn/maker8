## Why

A systematic investigation of the Maker8 codebase uncovered a cluster of correctness and reliability defects spanning the pipeline retry path, error handling, graceful shutdown, and the published JSON contract schemas. Left unaddressed these defects cause silent message loss, un-retried transient failures, stale health probes, and schema drift that breaks downstream tooling.

## What Changes

- Fix the `StageError` retry bug: exceptions raised inside the `except Exception` catch block cannot be re-caught by the sibling `except StageError`, so transient `OSError`s are never retried.
- Add `ge=1` validation to `render_max_attempts` in `Settings`; a value of `0` or negative makes `_execute_with_retry` loop zero times and silently return success (stage skipped).
- Emit to a dead-letter topic when a Kafka message cannot be handled (invalid `job_id`, JSON parse failure, pre-pipeline `ValueError`); currently the consumer logs and commits the offset with no DLQ record.
- Replace `os._exit(0)` on double-SIGINT with a clean shutdown sequence so `HealthManager.cleanup()` and the updater thread are guaranteed to run before process exit.
- Move Kafka `flush()` off the per-message hot path so it does not block the worker thread and eat into `kafka_max_poll_interval_ms` budget.
- Regenerate `docs/schemas/render_request.schema.json` and `docs/schemas/render_result.schema.json` from the Pydantic models; add the missing `subtitle`/`subtitles` fields, correct stale `account_ref`/`thumbnail_*` fields, and fix the `UploaderMetadata.visibility` default.
- Expose top-level re-exports from `src/render_contracts/__init__.py` (currently empty) so `from render_contracts import RenderRequest` works as documented.
- Add the eight missing identity assertions to `tests/test_contracts.py` `TestModelIdentity`.

## Capabilities

### New Capabilities

- `pipeline-retry-correctness`: Correct retry loop logic and enforce minimum `render_max_attempts` so transient failures are retried as configured and zero-attempt misconfigurations are rejected at startup.
- `dead-letter-queue`: Emit unhandleable Kafka messages to a configurable DLQ topic instead of silently committing them, ensuring no message is silently dropped.
- `graceful-shutdown`: Replace `os._exit(0)` with a coordinated shutdown sequence that guarantees cleanup hooks run before process exit.
- `contract-schema-sync`: Regenerate derived JSON schemas from Pydantic source models, fix `render_contracts` top-level exports, and close contract test coverage gaps.

### Modified Capabilities

## Impact

- `src/maker8/pipeline/orchestrator.py` — retry exception handling, `render_max_attempts` enforcement
- `src/maker8/config.py` — `ge=1` validator on `render_max_attempts`
- `src/maker8/kafka/consumer.py` — DLQ emit path for unhandleable messages
- `src/maker8/kafka/producer.py` — decouple `flush()` from per-message `send()`
- `src/maker8/app.py` — shutdown signal handler
- `src/render_contracts/__init__.py` — add top-level re-exports
- `docs/schemas/render_request.schema.json`, `docs/schemas/render_result.schema.json` — regenerated from Pydantic models
- `tests/test_contracts.py` — additional `TestModelIdentity` assertions
- `tests/fixtures/` — golden fixtures updated if model round-trips change
