## Why

Editor8 currently lacks a clear UI path for configuring and validating REST-first channel ingest authentication. This makes it harder for users to onboard REST-first ingest sources and verify that their ingest endpoint is ready.

## What Changes

- Add UI surfaces for REST-first channel ingest auth configuration in Editor8.
- Display auth status and validation feedback during REST-first channel setup.
- Make REST-first ingest authentication settings discoverable in the channel workflow.
- Ensure the UI matches existing Editor8 channel ingest patterns and clearly distinguishes REST-first auth from other channel types.

## Capabilities

### New Capabilities
- `rest-first-channel-ingest-auth-ui`: Provide Editor8 UI support for configuring REST-first channel ingest authentication and displaying validation/status feedback.

### Modified Capabilities
- `none`: No existing requirement-level specs are changing; this is a new UI capability.

## Impact

- Affected code: frontend Editor8 channel configuration and ingest management UI components.
- APIs: potential backend support for REST-first ingest auth metadata and validation state.
- Dependencies: editor8 frontend routing, forms, validation logic, and channel ingest workflows.
- Systems: REST-first channel ingest onboarding and auth verification.
