## ADDED Requirements

### Requirement: Unhandleable messages are emitted to a dead-letter topic

The Kafka consumer SHALL emit any message it cannot handle to a configurable dead-letter topic before committing the source offset. An unhandleable message is one that causes a `JSONDecodeError`, a `ValueError` during `PipelineContext.from_request()`, or any other exception that prevents the pipeline from starting.

#### Scenario: JSON parse failure produces a DLQ record
- **WHEN** the consumer receives a Kafka message whose value is not valid JSON
- **THEN** the consumer SHALL emit a DLQ record to the dead-letter topic containing the raw payload (base64-encoded), the error type, the error message, the source topic name, partition, and offset
- **THEN** the consumer SHALL commit the source offset only after the DLQ record is successfully produced

#### Scenario: Invalid job_id produces a DLQ record
- **WHEN** the consumer receives a valid JSON message but `PipelineContext.from_request()` raises `ValueError` (e.g. malformed `job_id`)
- **THEN** the consumer SHALL emit a DLQ record to the dead-letter topic with the same envelope fields
- **THEN** the consumer SHALL commit the source offset only after the DLQ record is successfully produced

#### Scenario: DLQ emit failure does not commit the source offset
- **WHEN** the dead-letter topic emit raises an exception (e.g. broker unavailable)
- **THEN** the consumer SHALL NOT commit the source offset, allowing the message to be re-delivered on the next poll

### Requirement: Dead-letter topic is configurable

The dead-letter topic name SHALL be configurable via the environment variable `MAKER8_DLQ_TOPIC`. If not set, the system SHALL default to `"maker8.dead-letter"`.

#### Scenario: Default dead-letter topic is used when env var is absent
- **WHEN** `MAKER8_DLQ_TOPIC` is not set in the environment
- **THEN** the consumer SHALL emit DLQ records to the topic named `"maker8.dead-letter"`

#### Scenario: Custom dead-letter topic is used when env var is set
- **WHEN** `MAKER8_DLQ_TOPIC` is set to `"my-custom-dlq"`
- **THEN** the consumer SHALL emit DLQ records to the topic named `"my-custom-dlq"`

### Requirement: Dead-letter record contains structured error metadata

Each dead-letter record SHALL be a JSON object containing: `source_topic`, `source_partition`, `source_offset`, `error_type`, `error_message`, `raw_payload` (base64-encoded original message bytes), and `timestamp_utc` (ISO-8601).

#### Scenario: DLQ record has all required fields
- **WHEN** a DLQ record is emitted for an unhandleable message
- **THEN** the emitted JSON SHALL contain non-null values for `source_topic`, `source_partition`, `source_offset`, `error_type`, `error_message`, `raw_payload`, and `timestamp_utc`
