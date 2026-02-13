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
        """Register the built-in source connectors and effects shipped with Maker8."""
        from maker8.plugins.effects.blur import BlurEffect
        from maker8.plugins.effects.brightness_contrast import BrightnessContrastEffect
        from maker8.plugins.effects.chroma_key import ChromaKeyEffect
        from maker8.plugins.effects.color_overlay import ColorOverlayEffect
        from maker8.plugins.effects.fade import FadeEffect
        from maker8.plugins.effects.grayscale import GrayscaleEffect
        from maker8.plugins.effects.mirror import MirrorEffect
        from maker8.plugins.effects.rotate import RotateEffect
        from maker8.plugins.effects.slide import SlideEffect
        from maker8.plugins.effects.zoom_pan import ZoomPanEffect
        from maker8.plugins.sources.http_source import HttpSourceConnector
        from maker8.plugins.sources.youtube import YouTubeSourceConnector

        # Sources
        self.register_source("youtube", YouTubeSourceConnector())
        self.register_source("http", HttpSourceConnector())

        # Effects
        self.register_effect("effect:fade", FadeEffect())
        self.register_effect("effect:zoom_pan", ZoomPanEffect())
        self.register_effect("effect:blur", BlurEffect())
        self.register_effect("effect:brightness_contrast", BrightnessContrastEffect())
        self.register_effect("effect:slide", SlideEffect())
        self.register_effect("effect:color_overlay", ColorOverlayEffect())
        self.register_effect("effect:grayscale", GrayscaleEffect())
        self.register_effect("effect:rotate", RotateEffect())
        self.register_effect("effect:mirror", MirrorEffect())
        self.register_effect("effect:chroma_key", ChromaKeyEffect())
