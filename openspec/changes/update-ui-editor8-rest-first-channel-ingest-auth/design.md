## Context

Editor8 users need a clearer experience for configuring REST-first channel ingest authentication. The current channel ingest UI does not surface auth details or validation feedback in a way that makes the REST-first flow easy to complete and verify.

## Goals / Non-Goals

**Goals:**
- Provide a dedicated UI flow for REST-first channel ingest auth configuration.
- Show validation status and readiness for REST-first ingest endpoints.
- Keep the UI consistent with existing Editor8 channel and ingest patterns.

**Non-Goals:**
- Building a new REST ingest backend service.
- Replacing the existing channel ingest architecture.
- Adding support for non-auth-related ingest types beyond REST-first.

## Decisions

- Use a dedicated REST-first auth panel within the existing channel setup screen rather than a separate standalone page. This keeps the flow within the channel configuration context and reduces navigation overhead.
- Reuse existing form and validation components where possible, extending them for REST-first auth-specific fields. This minimizes frontend complexity and preserves Editor8 UX consistency.
- Represent auth readiness with an explicit validation state (`Pending`, `Valid`, `Invalid`) instead of only error messages. This gives users clearer feedback during configuration.
- If backend metadata is required, add a small REST-first ingest auth payload to the channel API rather than changing the core channel model. This isolates the change and keeps impact limited to ingest-related fields.

## Risks / Trade-offs

- [Risk] The REST-first auth UI could duplicate existing channel settings if not properly scoped.
  → Mitigation: Keep REST-first auth fields clearly labeled and only visible for REST-first channel types.
- [Risk] Validation state could become stale if the backend does not report current auth status.
  → Mitigation: include a user-triggered refresh action and display the last validation timestamp if available.
- [Risk] Introducing new UI fields may require backend changes that lag frontend delivery.
  → Mitigation: design the frontend to degrade gracefully if REST-first auth metadata is unavailable.

## Open Questions

- Should the REST-first auth UI support multiple auth methods, or only a single default method for the first iteration?
- What backend field names and validation payloads are available for REST-first channel auth status?
