## 1. Frontend UI Implementation

- [x] 1.1 Add REST-first channel ingest auth fields to the Editor8 channel configuration UI
- [x] 1.2 Add auth validation state display and guidance messaging to the REST-first ingest flow
- [x] 1.3 Ensure REST-first auth UI is only shown for REST-first channel types and matches existing channel workflow patterns

## 2. API and Data Support

- [x] 2.1 Add or extend backend payloads for REST-first ingest auth metadata if needed by the UI
- [x] 2.2 Ensure the UI can read and submit REST-first auth settings through the existing channel ingest API
- [x] 2.3 Add a refresh or validation trigger for REST-first auth status if the backend supports it

## 3. Validation and Testing

- [x] 3.1 Add frontend tests covering REST-first auth visibility, save behavior, and validation status display
- [x] 3.2 Add integration tests for REST-first channel ingest auth submission and response handling if backend support is added
- [x] 3.3 Verify the REST-first auth flow does not impact non-REST-first ingest channel workflows
