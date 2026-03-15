# Request Card: Maintain Contract Consistency Between `editor8` Output and `maker8` Input

## Summary

`editor8` produces `video.render.request.v1` messages that are consumed by `maker8`.
Today the two projects are only partially consistent:

- `editor8` has a canonical shared contract package in `backend/src/render_contracts/`
- `editor8` frontend types are generated from that canonical contract
- but `maker8` still keeps its own duplicated `RenderSpec` / `RenderRequest` models
- and some fields present in the shared wire contract are not actually honored by `maker8` runtime

This request asks for a full contract-governance improvement so that:

1. `editor8` output and `maker8` input are defined from one source of truth
2. compatibility is enforced automatically in CI
3. unsupported or ignored fields are either implemented or removed/versioned explicitly
4. future changes cannot silently break the pipeline boundary

---

## Why This Is Needed

### Current Evidence

Canonical shared contract already exists in `editor8`:

- `editor8/backend/src/render_contracts/render_spec.py`
- `editor8/backend/src/render_contracts/events.py`
- `editor8/backend/pyproject.toml` includes `src/render_contracts` in wheel packaging

`editor8` runtime and frontend already depend on that contract:

- `editor8/backend/src/editor8/models/render_spec.py` re-exports from `render_contracts`
- `editor8/frontend/src/types/generated.ts` is auto-generated from `render-contracts`
- `editor8/backend/src/editor8/pipeline/validator.py` contains rules explicitly justified by `maker8` constraints

But `maker8` does not consume that canonical contract package. It duplicates the models in:

- `maker8/src/maker8/models/spec.py`
- `maker8/src/maker8/models/contracts.py`

This creates schema drift risk at the exact integration boundary between the two systems.

### Concrete Drift Risks Already Visible

1. **Duplicate contract definitions**
   - `editor8/render_contracts/render_spec.py`
   - `maker8/models/spec.py`
   - `maker8/models/contracts.py`
   These can diverge silently even if field names look similar today.

2. **Field exists in wire contract but is not honored by consumer runtime**
   - `RenderRequest.result.topic` / `result.key` exist in the shared contract
   - `maker8` currently emits results to configured topics, not request-provided destinations
   - this is a contract/behavior mismatch, not just a docs issue

3. **Fields accepted by schema but only partially implemented in `maker8`**
   - examples: `Layer.align`, `Transition.type`
   - if `editor8` starts generating or editing these fields assuming they work, behavior will drift from spec

4. **Validation is producer-side only**
   - `editor8` tries to enforce constraints required by `maker8`
   - but there is no consumer-driven compatibility test that proves a real `editor8` payload is accepted and semantically honored by `maker8`

5. **No cross-repo CI gate**
   - a contract change in `editor8` can ship without proving `maker8` still parses and renders correctly
   - a `maker8` behavior change can invalidate assumptions embedded in `editor8` validator, assembler, or UI

---

## Problem Statement

The current integration relies too much on convention:

- shared contract models exist, but not all consumers actually use them
- producer-side validation exists, but cross-project compatibility is not enforced automatically
- several fields are present in schema/docs without a strong guarantee that `maker8` implements them

That is not maintainable as `editor8` and `maker8` evolve independently.

The boundary must be upgraded from "best effort alignment" to "explicitly owned and automatically verified contract compatibility".

---

## Required Improvements

### 1. Establish One Contract Source of Truth

`render_contracts` must become the only authoritative definition for:

- `RenderSpec`
- `RenderRequest`
- result topic constants / event envelopes

Required actions:

- remove model duplication from `maker8`
- make `maker8` import the same canonical contract package used by `editor8`
- ensure packaging/versioning makes that contract consumable by both projects consistently

Non-goal:

- do not keep “same fields, separate copies” as the long-term approach

### 2. Define Ownership of the Integration Boundary

Document and enforce which system owns what:

- `editor8` owns authoring, assembly, editing, validation-before-publish
- `maker8` owns execution semantics, render-time validation, and consumer behavior
- `render_contracts` owns the wire format and message compatibility surface

Every field in the wire contract must have one of these states:

- supported and implemented end-to-end
- deprecated with migration plan
- reserved / intentionally ignored, but explicitly documented as such

No field should remain in the ambiguous state of "present in schema but behavior undefined".

### 3. Close Existing Contract/Behavior Mismatches

Investigate and fix all mismatches of this class, not just one field:

- fields defined in `render_contracts` but ignored by `maker8`
- fields emitted by `editor8` that `maker8` accepts but does not truly implement
- fields whose validation rules differ between producer and consumer
- fields exposed in frontend/editor UX without corresponding runtime support

This includes, at minimum:

- `RenderRequest.result.*`
- `Layer.align`
- `Transition.type`
- any other field that is schema-valid but behaviorally ignored or only partially implemented

### 4. Add Consumer-Driven Contract Tests

Introduce automated tests that prove:

- a real `editor8`-produced `RenderRequest` can be parsed by `maker8`
- producer-side defaults match consumer expectations
- producer-side validation blocks payloads that `maker8` would reject
- supported fields are not only parseable but behaviorally honored by `maker8`

Test classes required:

- contract parse/round-trip tests
- golden payload compatibility tests
- producer-consumer integration tests
- regression tests for every previously discovered mismatch

Recommended golden fixtures:

- minimal valid render request
- multi-scene request
- request with audio tracks
- request with effects
- request with publish targets
- request using every supported optional field

### 5. Add Cross-Project CI Gates

CI must fail if contract compatibility is broken.

Minimum requirements:

- changing `render_contracts` must run tests in both `editor8` and `maker8`
- changing `maker8` consumer models/validation/render semantics must run compatibility checks against canonical `editor8` fixtures
- changing `editor8` assembler/validator/generated types must run compatibility checks against `maker8`

Recommended checks:

- schema generation drift check
- generated frontend types freshness check
- contract fixture validation in both repos
- end-to-end publish-to-consume smoke test in CI or a nightly environment

### 6. Introduce Contract Versioning Discipline

Any future incompatible boundary change must not be shipped as an implicit edit to `1.0`.

Required:

- define compatibility policy for additive vs breaking changes
- document when to introduce `v2`
- require migration notes for any contract-affecting PR
- require explicit review from both `editor8` and `maker8` owners for contract changes

### 7. Align Docs and Runtime

Documentation must match real behavior:

- if `maker8` honors a field, document how
- if `maker8` ignores a field, document that until fixed or removed
- if a field is deprecated, mark it in contract docs and migration notes

Do not leave docs claiming richer support than runtime actually provides.

---

## Implementation Direction

Preferred direction:

1. move `maker8` to import the canonical `render_contracts` package
2. keep one shared set of JSON fixtures for boundary tests
3. run compatibility tests from CI in both projects
4. remove or implement behavior for any ambiguous fields
5. treat contract changes as versioned product changes, not local refactors

Avoid:

- keeping duplicate Pydantic models in sync manually
- relying only on README/schema docs
- relying only on producer-side validation
- accepting fields that no runtime semantics supports

---

## Deliverables

- canonical shared contract used by both `editor8` and `maker8`
- removal of duplicate boundary models from `maker8`
- compatibility test suite spanning producer and consumer
- CI checks that fail on contract drift
- documented status of every currently ambiguous field
- fixes or deprecations for existing mismatches
- clear contract versioning and ownership policy

---

## Definition of Done

This card is complete only when all of the following are true:

- `editor8` and `maker8` use one contract source of truth for the render boundary
- no duplicated render-boundary models remain in active use
- at least one automated test proves a real `editor8` payload is consumable by `maker8`
- CI blocks merges that break boundary compatibility
- all currently known contract/behavior mismatches are either fixed or explicitly deprecated
- docs accurately describe supported behavior on both sides
- future contract changes require explicit versioning/review discipline

---

## Success Metric

After this improvement, a change to the render boundary should no longer be able to:

- silently break Kafka interoperability
- pass local tests in only one repo
- expose fields in `editor8` that `maker8` does not actually honor
- create undocumented drift between schema, docs, and runtime behavior

The integration between `editor8` and `maker8` should behave like a governed interface, not a loose convention.
