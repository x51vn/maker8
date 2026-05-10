## ADDED Requirements

### Requirement: Double-SIGINT triggers coordinated shutdown, not os._exit

The application SHALL handle a second SIGINT signal by requesting a coordinated shutdown via a threading event rather than calling `os._exit(0)`. This ensures that registered cleanup hooks (`HealthManager.cleanup()`, updater thread join, `atexit` handlers) execute before the process exits.

#### Scenario: First SIGINT initiates graceful shutdown
- **WHEN** the application receives a SIGINT signal for the first time
- **THEN** the application SHALL begin a graceful shutdown (existing behavior: stop consuming new messages, flush in-flight work)

#### Scenario: Second SIGINT signals the shutdown event instead of force-killing
- **WHEN** the application receives a second SIGINT signal while graceful shutdown is in progress
- **THEN** the application SHALL set the shutdown threading event to accelerate exit
- **THEN** the application SHALL NOT call `os._exit(0)`
- **THEN** cleanup hooks registered via `atexit` and `HealthManager.cleanup()` SHALL still execute

#### Scenario: Cleanup completes within the hard timeout
- **WHEN** the second SIGINT shutdown event is set
- **THEN** the application SHALL wait up to 5 seconds for cleanup to complete
- **THEN** the application SHALL exit via `sys.exit(0)` after cleanup completes

#### Scenario: Cleanup exceeds the hard timeout
- **WHEN** cleanup has not completed within 5 seconds after the shutdown event is set
- **THEN** the application SHALL exit via `sys.exit(1)` to signal an unclean shutdown

### Requirement: Health status file is removed on clean exit

The `HealthManager` SHALL remove the health-status file during its `cleanup()` method so that liveness probes do not report healthy after the process has exited.

#### Scenario: Status file is absent after clean shutdown
- **WHEN** the application exits via the coordinated shutdown path
- **THEN** the health-status file SHALL no longer exist on disk after process exit
