# Style and conventions

- Python 3.11 with `from __future__ import annotations`
- Explicit type hints and strict mypy
- Ruff line length 100
- Keep `maker8.models` as a compatibility layer; canonical wire types live in `src/render_contracts/`
- When contract models change, update golden fixtures in `tests/fixtures/` and derived docs artifacts in `docs/schemas/` and `docs/examples/`
- Settings live in `src/maker8/config.py`; README tables may lag code