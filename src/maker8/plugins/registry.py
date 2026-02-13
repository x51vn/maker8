"""Central plugin registry (singleton-style).

Usage::

    registry = PluginRegistry()
    registry.load_defaults()

    connector = registry.get_source("youtube")
    plan = connector.resolve(asset_id, source_dict)
    local = connector.download(plan, dest)
"""

from __future__ import annotations

from maker8.plugins.base import EffectPlugin, SourceConnectorPlugin
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class PluginRegistry:
    """Look-up tables for source connectors and effect plugins."""

    def __init__(self) -> None:
        self._sources: dict[str, SourceConnectorPlugin] = {}
        self._effects: dict[str, EffectPlugin] = {}

    # ── Registration ─────────────────────────────────────────────────

    def register_source(self, kind: str, plugin: SourceConnectorPlugin) -> None:
        self._sources[kind] = plugin
        log.info("plugin.source.registered", kind=kind, id=plugin.manifest().id)

    def register_effect(self, plugin_id: str, plugin: EffectPlugin) -> None:
        self._effects[plugin_id] = plugin
        log.info("plugin.effect.registered", id=plugin_id)

    # ── Look-up ──────────────────────────────────────────────────────

    def get_source(self, kind: str) -> SourceConnectorPlugin:
        if kind not in self._sources:
            raise KeyError(f"No source connector registered for kind={kind!r}")
        return self._sources[kind]

    def get_effect(self, plugin_id: str) -> EffectPlugin:
        if plugin_id not in self._effects:
            raise KeyError(f"No effect plugin registered with id={plugin_id!r}")
        return self._effects[plugin_id]

    # ── Bootstrap ────────────────────────────────────────────────────

    def load_defaults(self) -> None:
        """Register the built-in source connectors shipped with Maker8."""
        from maker8.plugins.sources.http_source import HttpSourceConnector
        from maker8.plugins.sources.youtube import YouTubeSourceConnector

        self.register_source("youtube", YouTubeSourceConnector())
        self.register_source("http", HttpSourceConnector())
