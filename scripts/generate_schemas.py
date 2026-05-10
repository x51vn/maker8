"""Generate JSON Schema files from the canonical Pydantic models.

Run from the project root (with the venv active)::

    python scripts/generate_schemas.py

Writes:
    docs/schemas/render_request.schema.json
    docs/schemas/render_result.schema.json

These files are **derived artifacts** – always regenerate them after
changing models in ``src/render_contracts/`` or ``src/maker8/models/``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path when run directly.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from maker8.models.contracts import RenderResult  # noqa: E402
from render_contracts.render_spec import RenderRequest  # noqa: E402

SCHEMAS_DIR = ROOT / "docs" / "schemas"
SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)


def _write_schema(model: type, path: Path) -> None:
    schema = model.model_json_schema()
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}")


def main() -> None:
    print("Generating JSON schemas…")
    _write_schema(RenderRequest, SCHEMAS_DIR / "render_request.schema.json")
    _write_schema(RenderResult, SCHEMAS_DIR / "render_result.schema.json")
    print("Done.")


if __name__ == "__main__":
    main()
