# Maker8 Agent Notes

- Source of truth for settings is `src/maker8/config.py`; README tables can lag the code.
- `src/render_contracts/` holds the canonical wire models shared with editor8; `maker8.models` re-exports them for compatibility.
- `docs/schemas/*.json` and `docs/examples/*.json` are derived contract artifacts; update them together with model changes.
- The worker is synchronous and stage-based: `VALIDATE -> RESOLVE_ASSETS -> DOWNLOAD -> NORMALIZE -> TTS -> RENDER -> UPLOAD_DROPBOX -> EMIT_RESULT`.
- `openspec/changes/<name>/` is spec-driven work. For an active change, read `proposal.md`, `design.md`, and `tasks.md` before editing, then keep task checkboxes in sync.

## Commands

- Setup: `python -m venv venv && source venv/bin/activate && pip install -e ".[dev]"`
- Run worker: `maker8` or `python -m maker8.app`
- Full verification: `python -m pytest tests/`
- Focused test: `python -m pytest tests/test_contracts.py`
- Lint: `ruff check src/`
- Regenerate contract schemas: `python scripts/generate_schemas.py`
- Build image: `docker build -t maker8:latest .`

## Conventions

- Python 3.11, `from __future__ import annotations`, explicit type hints, strict mypy, Ruff line length 100.
- Keep `maker8.models` as a compatibility layer; do not move wire-format types out of `render_contracts` without updating re-exports and tests.
- When contract models change, update golden fixtures under `tests/fixtures/` and the derived docs artifacts together.

## Workflow

- There is no checked-in Makefile or task runner; run commands directly.
- `MAKER8_CREDENTIAL_SOURCE=db` is the default. Local runs need `MAKER8_EDITOR8_DATABASE_URL` or an intentional switch to legacy env-file mode.
- Startup probes FFmpeg and `yt-dlp` early and fails fast on missing required credentials or runtime dependencies.
