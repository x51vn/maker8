# Instructions: Improve `maker8` Logging and Failure Observability

## Summary

`maker8` needs more detailed structured logs.

The current failure below is a good example of why:

```json
{
  "status": "FAILED",
  "error": {
    "code": "RESOLVE_FAILED",
    "stage": "RESOLVE_ASSETS",
    "message": "Failed to resolve asset yt_5XGgp5Ltk7I: expected str, bytes or os.PathLike object, not NoneType"
  }
}
```

This tells us:

- the pipeline failed in `RESOLVE_ASSETS`
- it involved asset `yt_5XGgp5Ltk7I`
- some `None` value reached a path/subprocess boundary

But it does **not** tell us:

- which exact source payload caused the error
- which connector was used
- which command arguments were built
- whether `source.options.format` or another field was `null`
- which Kafka partition/offset produced the job
- which retry attempt failed
- how long the stage ran before failing

That is not enough for fast incident diagnosis.

---

## Likely Cause In This Incident

The most likely cause is in the YouTube source connector:

- `source.options.format` can be `null`
- `connector.resolve()` builds `fmt = options.get("format", _DEFAULT_FORMAT)`
- if the key exists with value `None`, `fmt` becomes `None` instead of the default
- `subprocess.run([... "-f", fmt, url])` then receives `None` in the command list
- Python raises `expected str, bytes or os.PathLike object, not NoneType`

This is exactly the kind of issue that should be obvious from logs without requiring source-code inspection.

Logging improvements should make this failure diagnosable from runtime logs alone.

---

## Current Gaps

### 1. Stage logs are too shallow

Examples today:

- `resolve.ok` logs only `asset_id` and `kind`
- `orchestrator.failed` logs only `job_id`, `stage`, `code`, `message`
- connector logs do not capture enough pre-failure context

### 2. No standardized stage lifecycle logging

There is no consistent pair of:

- `stage.start`
- `stage.success`
- `stage.failure`

with shared fields such as:

- `job_id`
- `job_key`
- `correlation_id`
- `stage`
- `attempt`
- `duration_ms`

### 3. Asset-level context is missing

For asset-related failures we should always know:

- `asset_id`
- `asset_type`
- `source_kind`
- sanitized `source` payload
- connector/plugin id

### 4. Kafka provenance is missing from job logs

When debugging a failed job we should be able to correlate it back to:

- topic
- partition
- offset
- message key

### 5. Command execution context is missing

For `yt-dlp`, `ffmpeg`, and `ffprobe` calls, logs should include:

- executable name
- sanitized arguments
- timeout
- return code
- stderr excerpt on failure

### 6. Retry logs are incomplete

Retries currently log stage and delay, but not enough context to understand:

- what failed
- which attempt produced which error
- whether the error message changed between attempts

---

## Required Improvements

### 1. Introduce Standard Log Fields Across the Pipeline

Every pipeline log related to a specific job should include a consistent minimum context:

- `job_id`
- `job_key` if known
- `correlation_id`
- `stage` when applicable
- `attempt` when applicable

For asset-level logs, also include:

- `asset_id`
- `source_kind`
- `asset_type` if available

For scene-level logs, also include:

- `scene_id`

This should be implemented either by:

- binding context on the logger at job/stage boundaries
- or consistently passing these fields in every log call

Preferred direction:

- bind job-level context once in orchestrator
- bind stage-level context in `_execute_with_retry()`

### 2. Add Stage Lifecycle Logs

Every stage should emit:

- `stage.start`
- `stage.success`
- `stage.failure`

Minimum fields:

- `job_id`
- `stage`
- `attempt`
- `duration_ms`

For failures:

- `error_code`
- `error_type`
- `error_message`
- full exception info

This should apply to:

- `VALIDATE`
- `RESOLVE_ASSETS`
- `DOWNLOAD`
- `NORMALIZE`
- `TTS`
- `RENDER`
- `UPLOAD_DROPBOX`
- `EMIT_RESULT`

### 3. Improve `RESOLVE_ASSETS` Logging Specifically

This stage is currently too opaque.

For each asset, log:

- `resolve.asset.start`
- `resolve.asset.success`
- `resolve.asset.failure`

Required fields:

- `job_id`
- `asset_id`
- `asset_type`
- `source_kind`
- sanitized `source` payload
- connector class name

On failure also log:

- `error_type`
- `error_message`
- stack trace

For the exact YouTube case, logs must make it obvious whether:

- `source.url` was empty
- `source.options.format` was `null`
- `source.options.max_duration_sec` was invalid
- command construction produced an invalid argument list

### 4. Add Connector-Specific Logs

#### YouTube connector

Before running `yt-dlp --dump-json`, log:

- `asset_id`
- `url`
- `format_spec`
- `max_duration_sec`
- timeout
- sanitized command

On subprocess failure, log:

- `returncode`
- stderr excerpt
- stdout excerpt if useful
- exception type

Also add a preflight validation log/error when:

- `format_spec is None`
- any command argument is not `str | bytes | os.PathLike`

This should fail with an explicit message such as:

- `Invalid yt-dlp command argument: format_spec is null`

instead of the generic Python `NoneType` error.

#### HTTP connector

For HTTP downloads, log:

- URL
- destination path
- response status
- response content type if available
- total downloaded bytes
- early abort reason if size limit is hit

#### FFmpeg / ffprobe call sites

For normalize/render helpers, log:

- command name
- timeout
- input path(s)
- output path
- return code
- stderr excerpt on failure

### 5. Improve Kafka Consumer Provenance Logs

When a message is received, log:

- topic
- partition
- offset
- key
- payload size

When handler execution starts and ends, log:

- same Kafka provenance fields
- `job_id` if parsed successfully
- result status: `success` / `failed`

When commit happens, log:

- topic
- partition
- offset
- commit outcome

This will make it possible to trace a failed render result back to the exact Kafka message.

### 6. Add Better Retry Logs

On every retryable failure, log:

- `job_id`
- `stage`
- `attempt`
- `max_attempts`
- `delay_sec`
- `error_code`
- `error_type`
- `error_message`

On final exhaustion, log a dedicated event:

- `stage.retry_exhausted`

Do not rely only on the final `orchestrator.failed` event.

### 7. Improve Final Failure Summary Logs

Before sending `FAILED` result / DLQ, log a full failure summary including:

- `job_id`
- `job_key`
- `correlation_id`
- `stage`
- `attempt`
- `error_code`
- `error_type`
- `error_message`
- known asset or scene context if available

This should be the main event operators search for in production.

### 8. Redact Sensitive Data Properly

More logs must not mean leaking secrets.

Never log:

- Kafka usernames/passwords
- Dropbox tokens
- ElevenLabs API keys
- Google credential file contents

Allowed:

- key filenames
- boolean presence flags
- token/key last 4 characters only if absolutely needed

For URLs:

- log sanitized URLs if query strings may contain secrets

For commands:

- log arguments after redaction, not raw secret-bearing env/config values

---

## Recommended Event Names

Suggested event vocabulary:

- `consumer.message_received`
- `consumer.handler_started`
- `consumer.handler_finished`
- `consumer.commit_succeeded`
- `consumer.commit_failed`
- `stage.start`
- `stage.success`
- `stage.failure`
- `stage.retry_scheduled`
- `stage.retry_exhausted`
- `resolve.asset.start`
- `resolve.asset.success`
- `resolve.asset.failure`
- `subprocess.start`
- `subprocess.success`
- `subprocess.failure`
- `job.failure_summary`

Use stable event names so logs are queryable in dashboards.

---

## Recommended Implementation Areas

Primary files to update:

- `src/maker8/kafka/consumer.py`
- `src/maker8/pipeline/orchestrator.py`
- `src/maker8/pipeline/resolve.py`
- `src/maker8/pipeline/download.py`
- `src/maker8/pipeline/normalize.py`
- `src/maker8/pipeline/tts.py`
- `src/maker8/pipeline/render.py`
- `src/maker8/pipeline/upload.py`
- `src/maker8/pipeline/emit.py`
- `src/maker8/plugins/sources/youtube.py`
- `src/maker8/plugins/sources/http_source.py`
- `src/maker8/utils/logging.py`

Optional but recommended:

- add small helper utilities for:
  - command sanitization
  - stderr truncation
  - duration measurement
  - context binding

---

## Validation and Testing Requirements

After implementing logging improvements, verify with at least these scenarios:

1. valid YouTube render request
2. invalid YouTube request with `format = null`
3. missing/empty `url`
4. HTTP download failure
5. FFmpeg normalize failure
6. TTS timeout
7. Dropbox upload failure

For each scenario confirm that logs alone answer:

- what failed
- where it failed
- why it failed
- which input entity caused it
- whether a retry will happen
- which Kafka message and job were involved

### Special regression case

Add a regression scenario for the incident above:

- asset source kind `youtube`
- `options.format = null`

Expected outcome:

- failure log explicitly identifies `format_spec` as null/invalid
- no generic `expected str, bytes or os.PathLike object, not NoneType` should be the first actionable clue

---

## Definition of Done

This logging improvement is complete only when:

- every stage has start/success/failure logs with consistent context
- connector failures include enough asset/source context to diagnose without code reading
- subprocess failures log command + timeout + stderr excerpt
- Kafka provenance is included in job processing logs
- retries are fully traceable
- secrets remain redacted
- the `RESOLVE_ASSETS` incident class becomes directly diagnosable from logs alone

---

## Success Metric

After this work, an operator looking only at runtime logs should be able to answer within a few minutes:

- which job failed
- which asset/scene/stage caused it
- what exact invalid input or downstream command caused the failure
- whether the system retried and what happened on each attempt

If source-code inspection is still required for routine incident diagnosis, the logging is still insufficient.
