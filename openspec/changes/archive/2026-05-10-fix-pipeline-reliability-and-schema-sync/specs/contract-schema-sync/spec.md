## ADDED Requirements

### Requirement: Published JSON schemas match the Pydantic source models

The files `docs/schemas/render_request.schema.json` and `docs/schemas/render_result.schema.json` SHALL be generated directly from the Pydantic model definitions in `src/render_contracts/render_spec.py` and SHALL contain no fields that have been removed from the models and no missing fields that are present in the models.

#### Scenario: render_request schema contains subtitle fields
- **WHEN** the schema generation script is run
- **THEN** `docs/schemas/render_request.schema.json` SHALL contain the `subtitles` field under `Defaults` and the `subtitle` field under `Scene`

#### Scenario: render_result schema uses channel_id not account_ref
- **WHEN** the schema generation script is run
- **THEN** `docs/schemas/render_result.schema.json` SHALL contain `channel_id` in `PublishTarget` and SHALL NOT contain `account_ref`

#### Scenario: render_result schema has correct visibility default
- **WHEN** the schema generation script is run
- **THEN** `docs/schemas/render_result.schema.json` SHALL reflect `UploaderMetadata.visibility` default as `"public"` (matching the Pydantic model)

#### Scenario: render_request schema does not contain stale thumbnail fields
- **WHEN** the schema generation script is run
- **THEN** `docs/schemas/render_request.schema.json` SHALL NOT contain `thumbnail_ref`, `thumbnail_source_url`, or `thumbnail_strategy`

### Requirement: Schema generation is reproducible via a script

A script SHALL exist at `scripts/generate_schemas.py` that generates both JSON schema files from the Pydantic models and writes them to `docs/schemas/`. Running the script SHALL be idempotent.

#### Scenario: Running the script regenerates both schema files
- **WHEN** `python scripts/generate_schemas.py` is executed
- **THEN** both `docs/schemas/render_request.schema.json` and `docs/schemas/render_result.schema.json` SHALL be overwritten with schema derived from the current Pydantic models

### Requirement: render_contracts package exposes top-level imports

The `src/render_contracts/__init__.py` file SHALL re-export the primary contract types (`RenderRequest`, `RenderResult`, `RenderSpec`, `RenderSpecV2`) so that `from render_contracts import RenderRequest` succeeds without importing from a submodule path.

#### Scenario: Top-level import of RenderRequest succeeds
- **WHEN** a Python module executes `from render_contracts import RenderRequest`
- **THEN** the import SHALL succeed without raising `ImportError`

#### Scenario: Top-level import of RenderResult succeeds
- **WHEN** a Python module executes `from render_contracts import RenderResult`
- **THEN** the import SHALL succeed without raising `ImportError`

### Requirement: TestModelIdentity covers all canonical contract aliases

The `TestModelIdentity` test class in `tests/test_contracts.py` SHALL include identity assertions for all canonical type aliases exported by `maker8.models.contracts`, including `AssetSource`, `AssetSourceOptions`, `NarrationDefaults`, `SubtitleDefaults`, `SceneBoundary`, `SceneSubtitle`, `SourceAttribution`, and `UploaderMetadata`.

#### Scenario: AssetSource identity assertion exists
- **WHEN** the test suite runs
- **THEN** `TestModelIdentity` SHALL assert that `maker8.models.contracts.AssetSource` is the same object as the canonical type from `render_contracts`

#### Scenario: All eight missing types have identity assertions
- **WHEN** the test suite runs
- **THEN** `TestModelIdentity` SHALL contain assertions for `AssetSource`, `AssetSourceOptions`, `NarrationDefaults`, `SubtitleDefaults`, `SceneBoundary`, `SceneSubtitle`, `SourceAttribution`, and `UploaderMetadata`
