## ADDED Requirements

### Requirement: REST-first ingest auth UI exists
The system SHALL provide a REST-first channel ingest authentication UI in Editor8 that is accessible from the channel setup flow.

#### Scenario: Show REST-first auth configuration
- **WHEN** a user selects a channel type configured for REST-first ingest
- **THEN** the UI displays REST-first auth fields and guidance in the channel configuration panel

### Requirement: REST-first auth validation status is visible
The system SHALL display REST-first ingest authentication status and validation feedback in the channel ingest UI.

#### Scenario: Show auth validation result
- **WHEN** the user saves REST-first ingest auth settings
- **THEN** the UI shows whether authentication is valid or invalid, with clear next steps for resolution

### Requirement: REST-first auth UI aligns with existing channel workflows
The system SHALL keep the REST-first auth UI consistent with existing Editor8 channel ingest patterns and avoid creating a separate disconnected flow.

#### Scenario: Consistent UI flow
- **WHEN** a user configures a REST-first channel ingest source
- **THEN** the workflow remains in the channel configuration context and does not require navigation to an unrelated page
