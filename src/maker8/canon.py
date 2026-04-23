"""Canonicalization of RenderSpec and job-key computation.

Spec §5 defines eight deterministic rules so that the same logical spec
always hashes to the same ``job_key``.
"""

from __future__ import annotations

import json
from typing import Any

from maker8.models.spec import RenderSpec
from maker8.utils.hashing import sha256_bytes

# ── Public API ───────────────────────────────────────────────────────────────


def canonicalize(spec: RenderSpec) -> str:
    """Return the canonical JSON string of *spec*.

    Rules (§5.1):
      1. UTF-8 serialize
      2. Sort object keys lexicographically
      3. Sort ``assets[]`` by ``id``
            4. Sort ``publish.targets[]`` by ``(platform, channel_id, channel_url, channel_name)``
      5. Keep order of ``scenes[]``
      6. Keep order of ``layers[]``
      7. Normalize floats to 6 decimal places
      8. Normalize ``\\r\\n`` → ``\\n``
    """
    data = spec.model_dump(mode="json", by_alias=True)

    # Rule 3
    if "assets" in data:
        data["assets"] = sorted(data["assets"], key=lambda a: a.get("id", ""))

    # Rule 4
    publish = data.get("publish") or {}
    if "targets" in publish:
        publish["targets"] = sorted(
            publish["targets"],
            key=lambda t: (
                t.get("platform", ""),
                t.get("channel_id", ""),
                t.get("channel_url", ""),
                t.get("channel_name", ""),
            ),
        )

    # Rule 7  – walk tree before serialisation
    data = _normalise_floats(data)

    # Rule 2 + compact encoding
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    # Rule 8
    raw = raw.replace("\\r\\n", "\\n")

    return raw


def compute_job_key(spec: RenderSpec) -> str:
    """``job_key = "sha256:" + hex(SHA-256(canonicalize(spec)))``."""
    canon = canonicalize(spec)
    digest = sha256_bytes(canon.encode("utf-8"))
    return f"sha256:{digest}"


# ── Internal helpers ─────────────────────────────────────────────────────────


def _normalise_floats(obj: Any) -> Any:
    """Recursively round every ``float`` to 6 decimal places."""
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: _normalise_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalise_floats(v) for v in obj]
    return obj
