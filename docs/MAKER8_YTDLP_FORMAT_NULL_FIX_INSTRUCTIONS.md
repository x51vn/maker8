# Instructions: Fix `format=null` YouTube Resolve Failures and Useless Retries

## Summary

`maker8` is currently failing jobs in `RESOLVE_ASSETS` for YouTube assets like this:

```text
Failed to resolve asset yt_bvQMGZj02wo: Invalid yt-dlp format spec: None
```

The failure is deterministic and happens almost instantly.
The worker then retries the same invalid input multiple times, which wastes time and makes operations noisier.

This must be fixed at the integration boundary, not only in logs.

---

## Root Cause

### What is happening

For YouTube assets, `render_contracts` defines:

- `AssetSourceOptions.format: str | None = None`

So the field is optional and may legally be absent or `null` in the payload model.

`editor8` publishes payloads using:

```python
model_dump(mode="json", by_alias=True)
```

That means optional fields can appear on the wire as:

```json
"options": {
  "format": null
}
```

instead of being omitted.

### Why `maker8` fails

In `maker8`'s YouTube connector:

- `options.get("format", _DEFAULT_FORMAT)` returns `None` if the key exists with `null`
- the connector now rejects that with an explicit error
- the stage wraps it as `RESOLVE_FAILED`
- retry policy still treats `RESOLVE_ASSETS` as retryable

So the system does this:

1. receives invalid-but-deterministic input
2. fails immediately
3. retries the exact same bad payload
4. fails again
5. keeps doing this until retry budget is exhausted

This is the wrong behavior for an input-contract issue.

---

## Desired Behavior

The system should treat:

- missing `options.format`
- `options.format = null`

as semantically equivalent unless the product explicitly chooses otherwise.

Recommended meaning:

- `null` or omitted `format` = use Maker8's default yt-dlp format string

This is the most robust and backward-compatible behavior.

At the same time:

- deterministic payload/config errors should not be retried

---

## Required Fixes

## 1. Fix `maker8` YouTube connector to normalize `None` to default

### File

- `src/maker8/plugins/sources/youtube.py`

### Current problem

The connector distinguishes:

- missing `format`
- explicit `format = None`

in a way that causes explicit `None` to fail.

### Required change

Normalize the value like this conceptually:

```python
raw_fmt = options.get("format")
fmt = raw_fmt or _DEFAULT_FORMAT
```

Or an equivalent implementation that ensures:

- `None` uses `_DEFAULT_FORMAT`
- empty string is either rejected explicitly or also normalized, depending on chosen policy

### Recommended policy

- `None` -> default
- missing key -> default
- empty string -> explicit validation error

Reason:

- `None` is a serialization artifact of optional fields
- empty string is much more likely a real invalid user/config input

### Important note

Do not preserve the current behavior of hard-failing on `None`.
That behavior is brittle at the editor8 -> maker8 boundary.

---

## 2. Stop retrying deterministic input errors

### Files

- `src/maker8/pipeline/resolve.py`
- possibly `src/maker8/retry.py`
- possibly connector-level error taxonomy if introduced

### Current problem

`RESOLVE_ASSETS` is globally retryable.
That is reasonable for:

- network failures
- yt-dlp transient site issues
- rate limits

But it is wrong for:

- invalid `url`
- invalid `format`
- unsupported source config

### Required change

Introduce a distinction between:

- transient resolve failures
- deterministic validation/config failures

At minimum, these should be non-retryable:

- missing URL
- invalid/empty yt-dlp format spec
- invalid source config shape
- unsupported source kind

### Recommended implementation

Use `StageError(..., retryable=False)` for deterministic cases.

Examples:

- `INVALID_SOURCE_URL`
- `INVALID_SOURCE_CONFIG`
- `INVALID_YTDLP_FORMAT`

Do not collapse all of these into generic retryable `RESOLVE_FAILED`.

### Expected result

If payload is bad, the worker should:

1. fail once
2. emit a clear failed result / DLQ
3. stop retrying

---

## 3. Make upstream serialization less noisy

### Files

- `../editor8/backend/src/editor8/kafka/__init__.py`
- any place `editor8` serializes `RenderRequest` for Kafka

### Current problem

`editor8` uses:

```python
model_dump(mode="json", by_alias=True)
```

This tends to preserve `None` values into the wire payload.

### Required change

For Kafka publishing of `RenderRequest`, evaluate switching to:

```python
model_dump(mode="json", by_alias=True, exclude_none=True)
```

or an equivalent normalization step before publish.

### Why

This keeps wire payloads cleaner and avoids downstream ambiguity between:

- omitted optional value
- explicit `null`

### Important caution

Do not make this change blindly for all payloads without checking compatibility.

Before enabling `exclude_none=True` broadly:

- confirm no downstream consumer depends on explicit `null`
- confirm frontend/editor round-trip behavior is unaffected where relevant

### Recommended approach

Use `exclude_none=True` specifically at Kafka publish boundaries for render requests if that aligns with agreed wire semantics.

---

## 4. Define and document boundary semantics explicitly

### Problem

Right now the real semantic rule is unclear:

- is `format = null` valid?
- should it mean "default"?
- should it mean invalid?

This ambiguity caused the runtime bug.

### Required change

Document explicitly in the shared contract/boundary docs:

- `AssetSourceOptions.format` is optional
- omitted or `null` means use consumer default format
- empty string is invalid

If the team chooses a different policy, document that policy instead and enforce it on both sides.

### Minimum docs to update

- shared contract docs
- `maker8` source connector docs/comments
- contract fixtures or examples if needed

---

## 5. Add regression tests in `maker8`

### Files

- `tests/test_contracts.py`
- add new tests closer to connector behavior if needed

### Required test coverage

#### Case A: omitted format

Payload:

- YouTube asset
- no `options.format`

Expected:

- parse succeeds
- resolve uses default format

#### Case B: explicit `format = null`

Payload:

- YouTube asset
- `"format": null`

Expected:

- parse succeeds
- resolve uses default format
- no failure

#### Case C: empty string format

Payload:

- YouTube asset
- `"format": ""`

Expected:

- explicit validation failure
- failure is non-retryable

### Why

Without these tests, this bug will come back during future contract refactors.

---

## 6. Add compatibility/regression tests in `editor8`

### Files

- `../editor8/backend/tests/test_contract_compat.py`
- possibly `../editor8/backend/tests/test_models.py`
- possibly validator tests

### Required coverage

Add tests proving:

- a `RenderRequest` with optional `format=None` serializes in the agreed wire form
- the agreed wire form remains compatible with `maker8`

If the chosen wire policy is `exclude_none=True`, test that:

- `format=None` is omitted from published payload

If the chosen wire policy is “null allowed and treated as default”, test that:

- `maker8` accepts and normalizes it

---

## 7. Improve failure classification in result/DLQ

### Files

- `src/maker8/pipeline/resolve.py`
- `src/maker8/pipeline/orchestrator.py`

### Required change

Do not surface deterministic payload/config errors as vague generic failures.

Prefer codes like:

- `INVALID_SOURCE_URL`
- `INVALID_YTDLP_FORMAT`
- `INVALID_SOURCE_OPTIONS`

instead of only:

- `RESOLVE_FAILED`

### Why

Operators should be able to tell immediately whether the problem is:

- bad input from upstream
- transient external dependency failure
- internal worker bug

---

## Recommended Implementation Order

1. fix `maker8` connector normalization for `format=None`
2. mark deterministic resolve validation errors as non-retryable
3. add `maker8` regression tests
4. align `editor8` Kafka serialization policy
5. add `editor8` compatibility tests
6. update docs/contract semantics

This order gives immediate runtime relief first, then locks behavior in with tests.

---

## Verification Checklist

After implementation, verify all of the following:

### Runtime behavior

1. job with omitted YouTube format succeeds to resolve
2. job with `format = null` also succeeds to resolve
3. job with invalid empty format fails once and does not retry

### Logs

For invalid format cases, console logs should clearly say:

- `asset_id`
- `source_kind=youtube`
- actual `format_spec` value
- non-retryable classification if deterministic

### Result/DLQ

For deterministic bad input:

- failed result is emitted once
- DLQ is emitted once if configured
- no repeated retries for the same unchanged payload

### Compatibility

- `editor8` golden fixtures still pass
- `maker8` contract tests still pass
- no new schema drift is introduced

---

## Definition of Done

This issue is fixed only when:

- `format=null` no longer causes `maker8` to fail unnecessarily
- deterministic source-config errors do not consume retry budget
- the editor8 -> maker8 boundary has an explicit agreed semantic for optional source format
- regression tests exist on both sides of the boundary

The real fix is not “better error text”.
The real fix is:

- normalize optional wire data correctly
- classify deterministic errors correctly
- stop retrying bad input
