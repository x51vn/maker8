# Maker8 Agent Notes

- Source of truth for settings is `src/maker8/config.py`; README tables can lag the code.
- `src/render_contracts/` holds the canonical wire models shared with editor8; `maker8.models` re-exports them for compatibility.
- `docs/schemas/*.json` and `docs/examples/*.json` are derived contract artifacts; update them together with model changes.
- The worker is synchronous and stage-based: `VALIDATE -> RESOLVE_ASSETS -> DOWNLOAD -> NORMALIZE -> TTS -> RENDER -> UPLOAD_DROPBOX -> EMIT_RESULT`.
- `openspec/changes/<name>/` is spec-driven work. For an active change, read `proposal.md`, `design.md`, and `tasks.md` before editing, then keep task checkboxes in sync. All currently known changes are under `openspec/changes/archive/`.

## Commands

- Setup: `python -m venv venv && source venv/bin/activate && pip install -e ".[dev]"`
- Run worker: `maker8` or `python -m maker8.app`
- Full verification: `python -m pytest tests/`
- Focused test: `python -m pytest tests/test_contracts.py`
- Lint: `ruff check src/`
- Format: `ruff format src/`
- Type-check: `mypy src/` (strict mode + pydantic plugin; fix all errors before committing)
- Regenerate contract schemas: `python scripts/generate_schemas.py`
- Build image: `docker build -t maker8:latest .`

**Recommended verification order**: `ruff check src/` → `mypy src/` → `python -m pytest tests/`

## Conventions

- Python 3.11, `from __future__ import annotations`, explicit type hints, strict mypy, Ruff line length 100.
- Keep `maker8.models` as a compatibility layer; do not move wire-format types out of `render_contracts` without updating re-exports and tests.
- When contract models change, update golden fixtures under `tests/fixtures/` and the derived docs artifacts together.
- `MAKER8_LOG_FORMAT` defaults to `console` in `config.py` (the README table is stale and says `json`).

## Workflow

- There is no checked-in Makefile or task runner; run commands directly.
- `MAKER8_CREDENTIAL_SOURCE=db` is the default. Local runs need `MAKER8_EDITOR8_DATABASE_URL` or an intentional switch to legacy env-file mode.
- Startup probes FFmpeg and `yt-dlp` early and fails fast on missing required credentials or runtime dependencies.
- yt-dlp binary: the managed path is `MAKER8_YTDLP_BIN_DIR` (default `/opt/maker8/bin/yt-dlp`); `MAKER8_YTDLP_PATH` overrides it. Empty `ytdlp_path` triggers auto-detect (managed dir → PATH).
- `kafka_max_poll_interval_ms` defaults to 7 200 000 ms (2 h) to cover worst-case long CPU jobs; don't lower this without considering yt-dlp resolve + download + TTS + render time.

## Notable config.py fields not in README

- `perf_mode`: `"balanced"` (default) | `"quality"` | `"fast"` — controls proxy resolution, fps cap, effect allowance, encode preset.
- `normalize_max_audio_channels`: default `2` (stereo preserved); set `1` to force mono (legacy behaviour).
- `metrics_enabled`: default `False`; when `True`, Prometheus metrics served on port `metrics_port` (default `9108`).

## Deployment

- **Registry path**: `docker.x51.vn/x-ai/maker8:latest`, deployed to `<deployment-host>`.
- `deploy-production.sh` — pushes images to the private registry then restarts compose on the remote host.
- `deploy-direct.sh` — registry-bypass: tarballs the image, SCPs it to the host, then loads and restarts compose. Use when the registry is unavailable.
- Both scripts hard-code the SSH key at `/home/beou/deployment/worker-z440/ssh/id_ed25519`.
