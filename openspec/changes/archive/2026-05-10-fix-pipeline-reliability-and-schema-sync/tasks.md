## 1. Pipeline Retry Correctness

- [x] 1.1 Read `src/maker8/pipeline/orchestrator.py` lines 220–323 to confirm current exception-handling structure in `_execute_with_retry`
- [x] 1.2 Collapse the sibling `except StageError` / `except Exception` blocks into a single `except Exception` clause that classifies the exception inline (wrap non-`StageError` as retryable `StageError`) and applies retry logic uniformly
- [x] 1.3 Add `Field(ge=1, default=3)` to `Settings.render_max_attempts` in `src/maker8/config.py` and remove any manual range check if present
- [x] 1.4 Write unit tests covering: (a) transient `OSError` is retried up to `render_max_attempts` times, (b) non-retryable `StageError` is not retried, (c) `render_max_attempts=0` raises `ValidationError` at settings construction
- [x] 1.5 Run `python -m pytest tests/` and confirm no regressions

## 2. Dead-Letter Queue

- [x] 2.1 Add `dlq_topic: str = Field(default="maker8.dead-letter")` to `Settings` in `src/maker8/config.py` (env var `MAKER8_DLQ_TOPIC`) — `kafka_render_dlq_topic` already existed; no new field needed
- [x] 2.2 Read `src/maker8/kafka/consumer.py` lines 121–159 to map the full exception-handling surface (JSON parse, `ValueError`, and post-pipeline errors)
- [x] 2.3 Create `src/maker8/kafka/dlq.py` — DLQ infrastructure already existed in consumer.py and orchestrator.py; gap was only the `PipelineContext.from_request()` escape path
- [x] 2.4 In `consumer.py`, call `emit_dead_letter()` in the `except` blocks for JSON parse failure and `ValueError` from `PipelineContext.from_request()` — fixed by wrapping `PipelineContext.from_request()` in orchestrator.handle() with try/except that emits DLQ via `_send_invalid_payload_dlq()`
- [x] 2.5 Write unit tests covering: (a) JSON parse failure emits DLQ record and commits offset, (b) invalid `job_id` emits DLQ record and commits offset, (c) DLQ emit failure does NOT commit source offset — covered in `test_pipeline_retry.py::TestOrchestratorContextCreation` and existing `test_consumer_invalid_json.py`
- [x] 2.6 Run `python -m pytest tests/` and confirm no regressions

## 3. Graceful Shutdown

- [x] 3.1 Read `src/maker8/app.py` lines 283–350 to understand the current signal-handler and shutdown flow
- [x] 3.2 Introduce a `threading.Event` (`_shutdown_event`) in the app module; set it in the double-SIGINT handler instead of calling `os._exit(0)`
- [x] 3.3 Update the consumer's main poll loop (or app's `run()`) to check `_shutdown_event` and break cleanly when set
- [x] 3.4 Add a 5-second hard-timeout after `_shutdown_event` is set: wait for cleanup to complete, then call `sys.exit(0)` on success or `sys.exit(1)` on timeout
- [x] 3.5 Verify `HealthManager.cleanup()` and the updater thread `join()` are called in the clean-exit path; confirm the health-status file is removed on exit
- [x] 3.6 Write a unit test (or integration smoke test) confirming that a simulated double-SIGINT triggers shutdown without `os._exit` being called
- [x] 3.7 Run `python -m pytest tests/` and confirm no regressions

## 4. Kafka Producer Async Flush

- [x] 4.1 Read `src/maker8/kafka/producer.py` line 46 to confirm `flush()` is called synchronously on every `send()`
- [x] 4.2 Remove `flush()` from the `send()` method
- [x] 4.3 Add `flush()` call to the producer's `close()` / `__exit__` method if not already present
- [x] 4.4 Add `producer_flush_interval: int = Field(default=30)` to `Settings` (env var `MAKER8_PRODUCER_FLUSH_INTERVAL`, units: seconds)
- [x] 4.5 Call `flush()` periodically from the consumer loop (after each message or on elapsed time — whichever comes first at the configured interval)
- [x] 4.6 Run `python -m pytest tests/` and confirm no regressions

## 5. Contract Schema Sync

- [x] 5.1 Create `scripts/generate_schemas.py` that imports `RenderRequest` and `RenderResult` from `render_contracts`, calls `.model_json_schema()` on each, and writes the results to `docs/schemas/render_request.schema.json` and `docs/schemas/render_result.schema.json`
- [x] 5.2 Run `python scripts/generate_schemas.py` and inspect the output to confirm: `subtitles` field present under `Defaults`, `subtitle` field present under `Scene`, `channel_id` in `PublishTarget` (not `account_ref`), `UploaderMetadata.visibility` default is `"public"`, stale `thumbnail_ref`/`thumbnail_source_url`/`thumbnail_strategy` fields are absent
- [x] 5.3 Update `src/render_contracts/__init__.py` to re-export `RenderRequest`, `RenderResult`, `RenderSpec`, `RenderSpecV2` from `render_contracts.render_spec`
- [x] 5.4 Verify `from render_contracts import RenderRequest` and `from render_contracts import RenderResult` succeed in a Python shell
- [x] 5.5 Add identity assertions for the eight missing types to `TestModelIdentity` in `tests/test_contracts.py`: `AssetSource`, `AssetSourceOptions`, `NarrationDefaults`, `SubtitleDefaults`, `SceneBoundary`, `SceneSubtitle`, `SourceAttribution`, `UploaderMetadata`
- [x] 5.6 Run `python -m pytest tests/test_contracts.py` and confirm all new assertions pass
- [x] 5.7 Update `tests/fixtures/golden_*.json` if any round-trip golden fixtures need refreshing after schema changes
- [x] 5.8 Run `python -m pytest tests/` and confirm no regressions