## ADDED Requirements

### Requirement: Stage exceptions are always routed through the retry loop

The orchestrator's `_execute_with_retry` method SHALL classify every exception raised by a stage as a `StageError` before applying retry logic, ensuring that all exception types (including `OSError`, `IOError`, and other non-`StageError` subclasses) are subject to the configured retry count.

#### Scenario: Transient OSError is retried up to render_max_attempts times
- **WHEN** a pipeline stage raises `OSError` during execution
- **THEN** the orchestrator SHALL catch it, wrap it as a retryable `StageError`, and retry the stage up to `render_max_attempts - 1` additional times with exponential backoff before propagating the failure

#### Scenario: Non-retryable StageError is not retried
- **WHEN** a pipeline stage raises a `StageError` with `retryable=False`
- **THEN** the orchestrator SHALL propagate the error immediately without any retry attempt

#### Scenario: Retryable StageError is retried up to render_max_attempts times
- **WHEN** a pipeline stage raises a `StageError` with `retryable=True`
- **THEN** the orchestrator SHALL retry the stage up to `render_max_attempts - 1` additional times before propagating the failure

### Requirement: render_max_attempts must be at least 1

The `Settings` model SHALL enforce that `render_max_attempts` is greater than or equal to 1. The application SHALL reject startup if `MAKER8_RENDER_MAX_ATTEMPTS` is set to 0 or a negative value, producing a clear validation error message.

#### Scenario: Zero render_max_attempts is rejected at startup
- **WHEN** the environment variable `MAKER8_RENDER_MAX_ATTEMPTS` is set to `0`
- **THEN** the application SHALL raise a `ValidationError` at startup and exit with a non-zero code before consuming any messages

#### Scenario: Negative render_max_attempts is rejected at startup
- **WHEN** the environment variable `MAKER8_RENDER_MAX_ATTEMPTS` is set to a negative integer
- **THEN** the application SHALL raise a `ValidationError` at startup and exit with a non-zero code before consuming any messages

#### Scenario: Valid render_max_attempts allows startup
- **WHEN** `MAKER8_RENDER_MAX_ATTEMPTS` is set to a positive integer (e.g. `3`)
- **THEN** the application SHALL start successfully and use that value as the retry limit per stage
