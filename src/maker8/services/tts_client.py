"""TTS service – provider-agnostic narration synthesis.

The concrete TTS backend is selected at runtime via ``tts_preset_ref`` which
maps to a preset in ``config/tts_presets.json``.  The preset declares the
``provider`` key (``"gtts"``, ``"google_cloud"``, ``"elevenlabs"``, etc.)
and any provider-specific kwargs.
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


# ── Duration helper (shared by all providers) ────────────────────────────────


def _get_audio_duration(path: Path) -> float:
    """Return the duration in seconds of an audio file using MoviePy 2.x."""
    from moviepy import AudioFileClip

    clip = AudioFileClip(str(path))
    dur = clip.duration
    clip.close()
    return dur


# ── gTTS provider (default) ─────────────────────────────────────────────────


class GTTSProvider(TTSProvider):
    """Google-Translate TTS via the ``gTTS`` library (free, no API key)."""

    def synthesize(
        self,
        text: str,
        lang: str,
        output_path: Path,
        **kwargs: object,
    ) -> SynthesisResult:
        from gtts import gTTS

        short_lang = lang.split("-")[0]
        slow: bool = bool(kwargs.get("slow", False))

        tts = gTTS(text=text, lang=short_lang, slow=slow)
        tts.save(str(output_path))

        return SynthesisResult(
            audio_path=output_path,
            duration_sec=_get_audio_duration(output_path),
        )


# ── Google Cloud TTS provider ────────────────────────────────────────────────


class GoogleCloudTTSProvider(TTSProvider):
    """Google Cloud Text-to-Speech (high quality, requires API credentials).

    Preset kwargs:
        voice_name: str  – e.g. ``"vi-VN-Neural2-A"``
        speaking_rate: float – speed multiplier (default 1.0)
        pitch: float – semitones shift (default 0.0)
        audio_encoding: str – ``"MP3"`` | ``"LINEAR16"`` | ``"OGG_OPUS"``
    """

    def synthesize(
        self,
        text: str,
        lang: str,
        output_path: Path,
        **kwargs: object,
    ) -> SynthesisResult:
        from google.cloud import texttospeech

        voice_name = str(kwargs.get("voice_name", ""))
        speaking_rate = float(kwargs.get("speaking_rate", 1.0))
        pitch = float(kwargs.get("pitch", 0.0))
        encoding_name = str(kwargs.get("audio_encoding", "MP3"))

        encoding_map = {
            "MP3": texttospeech.AudioEncoding.MP3,
            "LINEAR16": texttospeech.AudioEncoding.LINEAR16,
            "OGG_OPUS": texttospeech.AudioEncoding.OGG_OPUS,
        }
        audio_encoding = encoding_map.get(
            encoding_name, texttospeech.AudioEncoding.MP3
        )

        client = texttospeech.TextToSpeechClient()

        synthesis_input = texttospeech.SynthesisInput(text=text)

        voice_params = texttospeech.VoiceSelectionParams(
            language_code=lang,
            name=voice_name or None,
        )

        audio_config = texttospeech.AudioConfig(
            audio_encoding=audio_encoding,
            speaking_rate=speaking_rate,
            pitch=pitch,
        )

        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice_params,
            audio_config=audio_config,
        )

        output_path.write_bytes(response.audio_content)

        return SynthesisResult(
            audio_path=output_path,
            duration_sec=_get_audio_duration(output_path),
        )


# ── ElevenLabs provider ─────────────────────────────────────────────────────


class ElevenLabsProvider(TTSProvider):
    """ElevenLabs TTS (premium AI voices).

    Requires env ``MAKER8_ELEVENLABS_API_KEY``.

    Preset kwargs:
        voice_id: str – ElevenLabs voice ID
        model_id: str – e.g. ``"eleven_multilingual_v2"``
        stability: float – 0.0–1.0 (default 0.5)
        similarity_boost: float – 0.0–1.0 (default 0.75)
        style: float – 0.0–1.0 (default 0.0)
    """

    def synthesize(
        self,
        text: str,
        lang: str,
        output_path: Path,
        **kwargs: object,
    ) -> SynthesisResult:
        from elevenlabs import ElevenLabs, VoiceSettings

        api_key = str(kwargs.get("api_key", ""))
        voice_id = str(kwargs.get("voice_id", "21m00Tcm4TlvDq8ikWAM"))
        model_id = str(kwargs.get("model_id", "eleven_multilingual_v2"))
        stability = float(kwargs.get("stability", 0.5))
        similarity_boost = float(kwargs.get("similarity_boost", 0.75))
        style = float(kwargs.get("style", 0.0))

        client = ElevenLabs(api_key=api_key) if api_key else ElevenLabs()

        audio_gen = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id=model_id,
            voice_settings=VoiceSettings(
                stability=stability,
                similarity_boost=similarity_boost,
                style=style,
            ),
        )

        with open(output_path, "wb") as fh:
            for chunk in audio_gen:
                fh.write(chunk)

        return SynthesisResult(
            audio_path=output_path,
            duration_sec=_get_audio_duration(output_path),
        )


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
        "google_cloud": GoogleCloudTTSProvider,
        "elevenlabs": ElevenLabsProvider,
    }

    def __init__(self, settings: Settings) -> None:
        self._preset_store = PresetStore(settings.tts_presets_path)
        self._default_provider = settings.tts_provider
        self._settings = settings

    def synthesize(
        self,
        text: str,
        lang: str,
        preset_ref: str,
        output_path: Path,
    ) -> SynthesisResult:
        """Synthesize *text* and write the audio to *output_path*."""
        preset = dict(self._preset_store.get(preset_ref))
        provider_name = preset.pop("provider", self._default_provider)

        provider_cls = self._PROVIDERS.get(provider_name)
        if provider_cls is None:
            raise ValueError(f"Unknown TTS provider: {provider_name!r}")

        # Inject ElevenLabs API key from settings if not in preset
        if provider_name == "elevenlabs" and "api_key" not in preset:
            preset["api_key"] = self._settings.elevenlabs_api_key

        provider = provider_cls()
        effective_lang = preset.pop("lang", lang)

        log.info(
            "tts.synthesize",
            provider=provider_name,
            lang=effective_lang,
            chars=len(text),
        )
        return provider.synthesize(text, effective_lang, output_path, **preset)
            chars=len(text),
        )
        return provider.synthesize(text, effective_lang, output_path, **preset)
