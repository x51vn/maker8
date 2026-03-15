# Instructions: Fix `maker8` MyPy CI Failures After Shared Contract Migration

## Summary

The current `mypy` failure is not primarily a business-logic problem.
It is a typing/export-surface issue introduced while `maker8` was being moved toward the shared `render_contracts` package.

There are 2 classes of failures:

1. `attr-defined` errors caused by re-exported symbols that are not explicitly exported under `mypy --strict`
2. `unused-ignore` errors caused by stale `# type: ignore[...]` comments that are no longer needed

This should be fixed cleanly at the module-export layer, not by adding more ignores.

---

## Observed Failures

Current CI output shows:

- `maker8.models.common` does not explicitly export `Trace`
- `maker8.models.common` does not explicitly export `PublishTarget`
- `maker8.models.contracts` does not explicitly export `RenderRequest`
- two `type: ignore` comments are now unused

Affected files:

- `src/maker8/models/common.py`
- `src/maker8/models/contracts.py`
- `src/maker8/models/__init__.py`
- `src/maker8/models/manifest.py`
- `src/maker8/pipeline/context.py`
- `src/maker8/pipeline/orchestrator.py`
- `src/maker8/plugins/effects/grayscale.py`
- `src/maker8/plugins/sources/http_source.py`

---

## Root Cause

`maker8` now re-exports canonical wire-format types from `render_contracts`, for example:

- `PublishTarget`, `Trace` from `render_contracts.render_spec` via `maker8.models.common`
- `RenderRequest`, `ResultDestination` from `render_contracts.render_spec` via `maker8.models.contracts`

Under `mypy --strict`, `no_implicit_reexport` is effectively enforced.
That means:

- importing a name into a module does **not** make it part of that module's public typed API
- other files importing that name from the re-exporting module will fail unless the module explicitly exports it

So the errors are expected until the re-export modules define an explicit public export surface.

---

## Required Fix

### 1. Add Explicit `__all__` to `src/maker8/models/common.py`

`common.py` currently re-exports `PublishTarget` and `Trace`, but does not declare them in `__all__`.

Add an explicit `__all__` that includes:

- `RenderStage`
- `PublishStage`
- `JobStatus`
- `PublishStatus`
- `ErrorInfo`
- `DropboxFileRef`
- `OutputMeta`
- `EngineVersions`
- `PublishTarget`
- `Trace`

Why:

- this makes the module’s typed public API explicit
- it resolves the `attr-defined` errors in:
  - `pipeline/context.py`
  - `models/manifest.py`
  - `models/contracts.py`
  - `models/__init__.py`

### 2. Add Explicit `__all__` to `src/maker8/models/contracts.py`

`contracts.py` re-exports `RenderRequest` and `ResultDestination`, but does not explicitly export them.

Add `__all__` including:

- `ResultDestination`
- `RenderRequest`
- `DropboxOutput`
- `RenderResult`
- `DLQPayload`

Why:

- this resolves the remaining `attr-defined` errors in:
  - `models/__init__.py`
  - `pipeline/orchestrator.py`

### 3. Keep Backward-Compatible Import Paths

Do **not** “fix” this by scattering direct imports everywhere unless that is part of a deliberate cleanup pass.

Preferred short-term fix:

- preserve existing import paths such as `from maker8.models.common import Trace`
- make the re-export modules typed correctly

Reason:

- this is aligned with the current migration goal of keeping `maker8` compatible while moving to shared contracts
- it avoids mixing architectural cleanup with CI break-fix work

### 4. Remove Stale `type: ignore` Comments

Remove these unused ignores:

- `src/maker8/plugins/effects/grayscale.py`
  - remove both `# type: ignore[no-any-return]`
- `src/maker8/plugins/sources/http_source.py`
  - remove `# type: ignore[import-untyped]` from the `requests` import

Why:

- CI is explicitly failing because those ignores are now unnecessary
- leaving them in place only hides future real typing issues

### 5. Do Not Add New Blanket Ignores

Do **not** fix this by:

- adding `# type: ignore[attr-defined]` at call sites
- disabling `no_implicit_reexport`
- loosening `mypy --strict`

That would hide a real public-API problem in the module layout.

---

## Suggested Implementation Order

1. edit `src/maker8/models/common.py`
2. edit `src/maker8/models/contracts.py`
3. remove stale ignores in:
   - `src/maker8/plugins/effects/grayscale.py`
   - `src/maker8/plugins/sources/http_source.py`
4. run `mypy`
5. run contract tests

This ordering should clear the export-surface failures first, then the cleanup failures.

---

## Verification Steps

After the edits, run:

```bash
mypy src/maker8
pytest -q tests/test_contracts.py
```

If CI runs a broader suite, also run:

```bash
pytest -q
```

Expected result:

- no `attr-defined` errors from `maker8.models.common`
- no `attr-defined` errors from `maker8.models.contracts`
- no `unused-ignore` errors

---

## Optional Follow-Up Cleanup

This is not required to make CI green, but is recommended after the immediate fix:

1. audit all `maker8.models.*` re-export modules and ensure every intended public symbol is declared in `__all__`
2. document the re-export policy clearly:
   - `render_contracts` owns wire-format types
   - `maker8.models.common` / `maker8.models.contracts` are compatibility re-export layers
3. add a small regression test that imports:
   - `Trace` from `maker8.models.common`
   - `PublishTarget` from `maker8.models.common`
   - `RenderRequest` from `maker8.models.contracts`
   and keeps `mypy` compatibility from regressing

---

## Definition of Done

This issue is fixed when:

- `mypy` passes without loosening strictness
- no new `type: ignore` is introduced for these failures
- `maker8` still exposes the intended backward-compatible imports
- contract tests still pass after the export cleanup

The correct fix is to make the public typed API explicit, not to suppress the checker.
