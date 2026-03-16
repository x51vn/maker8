"""Abstract base classes for all Maker8 plugins.

Two plugin families exist today:

* **SourceConnectorPlugin** – resolves and downloads external media.
* **EffectPlugin** – applies a visual/audio effect to an intermediate
  representation during the RENDER stage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Shared manifest ──────────────────────────────────────────────────────────


@dataclass
class PluginManifest:
    """Metadata every plugin must expose."""

    id: str
    version: str
    deterministic: bool = True


# ── Source Connector ─────────────────────────────────────────────────────────


@dataclass
class ResolvedAssetPlan:
    """Output of ``SourceConnectorPlugin.resolve()``.

    Carries enough information for the DOWNLOAD stage to fetch the file.
    """

    asset_id: str
    source_kind: str
    url: str
    filename: str
    expected_type: str  # "video" | "image" | "audio"
    format_spec: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceConnectorPlugin(ABC):
    """Resolve an asset reference to a downloadable plan, then download it."""

    @abstractmethod
    def manifest(self) -> PluginManifest: ...

    @abstractmethod
    def schema(self) -> dict[str, Any]: ...

    @abstractmethod
    def resolve(self, asset_id: str, source: dict[str, Any]) -> ResolvedAssetPlan:
        """Analyse the source and return a download plan."""
        ...

    @abstractmethod
    def download(self, plan: ResolvedAssetPlan, dest_dir: Path) -> Path:
        """Execute the plan and return the local file path."""
        ...


# ── Effect ───────────────────────────────────────────────────────────────────


class EffectPlugin(ABC):
    """Apply a deterministic effect to the scene's intermediate representation.

    Rules from Spec §8.1:
      - No network access
      - No subprocess spawning
      - Honour budgets
    """

    @abstractmethod
    def manifest(self) -> PluginManifest: ...

    @abstractmethod
    def schema(self) -> dict[str, Any]: ...

    @abstractmethod
    def apply(self, ctx: Any, ir: Any, instance: dict[str, Any]) -> Any:
        """Return the (possibly mutated) *ir*."""
        ...

    def has_ffmpeg_filter(self) -> bool:
        """Return True if this effect uses an FFmpeg filter instead of per-frame Python.

        Override in subclasses that are implemented as FFmpeg filters.
        Effects that return False may be skipped in ``fast`` perf mode.
        """
        return False
