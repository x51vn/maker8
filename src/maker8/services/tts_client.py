"""TTS service – provider-agnostic narration synthesis.

The concrete TTS backend is selected at runtime via ``tts_preset_ref`` which
maps to a preset in ``config/tts_presets.json``.  The preset declares the
``provider`` key (``"gtts"``, etc.) and any provider-specific kwargs.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from maker8.config import Settings
from maker8.utils.logging import get_logger

log = get_logger(__name__)


# ── Synthesis result ─────────────────────────────────────────────────────────


@dataclass
class SynthesisResult:
    """Value returned by every ``TTSProvider.synthesize()`` call."""

    audio_path: Path
    duration_sec: float


# ── Provider ABC ─────────────────────────────────────────────────────────────


class TTSProvider(ABC):
    @abstractmethod
    def synthesize(
        self,
        text: str,
        lang: str,
        output_path: Path,
        **kwargs: object,
    ) -> SynthesisResult:
        """Write speech audio to *output_path* and return its duration."""
        ...


# ── gTTS provider (default) ─────────────────────────────────────────────────


class GTTSProvider(TTSProvider):
    """Google-Translate TTS via the ``gTTS`` library."""

    def synthesize(
        self,
        text: str,
        lang: str,
        output_path: Path,
        **kwargs: object,
    ) -> SynthesisResult:
        from gtts import gTTS
        from moviepy.editor import AudioFileClip

        # gTTS uses short lang codes (e.g. "vi" not "vi-VN")
        short_lang = lang.split("-")[0]
        slow: bool = bool(kwargs.get("slow", False))

        tts = gTTS(text=text, lang=short_lang, slow=slow)
        tts.save(str(output_path))

        clip = AudioFileClip(str(output_path))
        duration = clip.duration
        clip.close()

        return SynthesisResult(audio_path=output_path, duration_sec=duration)


# ── Preset store ─────────────────────────────────────────────────────────────


class PresetStore:
    """Load ``tts_preset_ref`` → provider kwargs from a JSON file."""

    _DEFAULT_PRESET: dict = {"provider": "gtts", "lang": "vi"}

    def __init__(self, path: Path) -> None:
        self._presets: dict[str, dict] = {}
        if path.exists():
            self._presets = json.loads(path.read_text(encoding="utf-8"))

    def get(self, ref: str) -> dict:
        return self._presets.get(ref, self._DEFAULT_PRESET)


# ── Façade ───────────────────────────────────────────────────────────────────


class TTSService:
    """High-level entry point used by the TTS pipeline stage."""

    _PROVIDERS: dict[str, type[TTSProvider]] = {
        "gtts": GTTSProvider,
    }

    def __init__(self, settings: Settings) -> None:
        self._preset_store = PresetStore(settings.tts_presets_path)
        self._default_provider = settings.tts_provider

    def synthesize(
        self,
        text: str,
        lang: str,
        preset_ref: str,
        output_path: Path,
    ) -> SynthesisResult:
        """Synthesize *text* and write the audio to *output_path*."""
        preset = self._preset_store.get(preset_ref)
        provider_name = preset.get("provider", self._default_provider)

        provider_cls = self._PROVIDERS.get(provider_name)
        if provider_cls is None:
            raise ValueError(f"Unknown TTS provider: {provider_name!r}")

        provider = provider_cls()
        effective_lang = preset.get("lang", lang)

        log.info(
            "tts.synthesize",
            provider=provider_name,
            lang=effective_lang,
            chars=len(text),
        )
        return provider.synthesize(text, effective_lang, output_path, **preset)
