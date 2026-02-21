"""TTS service – provider-agnostic narration synthesis.

Architecture
~~~~~~~~~~~~
* **TTSProvider** (ABC) – one ``synthesize()`` per provider.
* **KeyRing** – thread-safe round-robin credential rotation.
* **TTSService** – façade used by the pipeline; resolves presets,
  picks the right provider, and injects the *next* credential from the
  corresponding ``KeyRing`` (if available).

Credential rotation
~~~~~~~~~~~~~~~~~~~
Both *Google Cloud TTS* and *ElevenLabs* support **multiple API keys /
service accounts** that are rotated per-video (one ``next()`` call per
``synthesize_video()`` invocation from the TTS pipeline stage).

* **Google Cloud** – each key is a service-account JSON file placed in
  ``gg-tts-keys/``.  The provider creates a client from the file path
  returned by the ring.
* **ElevenLabs** – each key is a plain-text API key placed in
  ``elevenlabs-keys/`` (one key per ``.txt`` / ``.key`` file).

If the key directory is empty or missing the provider falls back to a
single credential from ``Settings`` (backward-compatible).
"""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maker8.config import Settings
from maker8.services.key_ring import KeyRing
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
    """Base class for all TTS backends.

    Subclasses **must** implement ``synthesize()``.  They receive the
    credential for the current video via ``**kwargs`` (e.g.
    ``credentials_path`` for Google Cloud, ``api_key`` for ElevenLabs).
    """

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


# ── Duration helper (shared) ────────────────────────────────────────────────


def _get_audio_duration(path: Path) -> float:
    """Return the duration in seconds of an audio file.

    Uses ``ffprobe`` for speed (no full decode required).  Falls back to
    ``AudioFileClip`` if ffprobe is unavailable or cannot parse the file,
    so unusual codecs produced by TTS providers remain supported.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    # Fallback: MoviePy handles edge-cases ffprobe may reject.
    from moviepy import AudioFileClip

    clip = AudioFileClip(str(path))
    dur = clip.duration
    clip.close()
    return float(dur)


# ── gTTS provider (default, free) ───────────────────────────────────────────


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
    """Google Cloud Text-to-Speech with round-robin service-account rotation.

    Credentials are supplied per-call via the ``credentials_path`` kwarg
    (a ``Path`` to a service-account JSON file).  If absent the provider
    falls back to *Application Default Credentials* (ADC).

    Preset kwargs (from ``tts_presets.json``):
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

        # ── Credential handling ──────────────────────────────────────
        credentials_path = kwargs.pop("credentials_path", None)
        client = self._build_client(credentials_path)

        # ── Timeout ──────────────────────────────────────────────────
        # Injected by TTSService; falls back to a conservative default.
        timeout_sec = float(kwargs.pop("timeout", 120.0))  # type: ignore[arg-type]

        # ── Voice / audio parameters ────────────────────────────────
        voice_name = str(kwargs.get("voice_name", ""))
        speaking_rate = float(kwargs.get("speaking_rate", 1.0))  # type: ignore[arg-type]
        pitch = float(kwargs.get("pitch", 0.0))  # type: ignore[arg-type]
        encoding_name = str(kwargs.get("audio_encoding", "MP3"))

        encoding_map = {
            "MP3": texttospeech.AudioEncoding.MP3,
            "LINEAR16": texttospeech.AudioEncoding.LINEAR16,
            "OGG_OPUS": texttospeech.AudioEncoding.OGG_OPUS,
        }
        audio_encoding = encoding_map.get(
            encoding_name, texttospeech.AudioEncoding.MP3
        )

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

        response = client.synthesize_speech(  # type: ignore[attr-defined]
            input=synthesis_input,
            voice=voice_params,
            audio_config=audio_config,
            timeout=timeout_sec,
        )
        output_path.write_bytes(response.audio_content)

        return SynthesisResult(
            audio_path=output_path,
            duration_sec=_get_audio_duration(output_path),
        )

    # ── Internal ─────────────────────────────────────────────────────

    @staticmethod
    def _build_client(
        credentials_path: object,
    ) -> object:  # texttospeech.TextToSpeechClient
        """Create a TTS client, optionally from a specific service-account."""
        from google.cloud import texttospeech
        from google.oauth2 import service_account

        if credentials_path and Path(str(credentials_path)).is_file():
            creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                str(credentials_path),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            log.debug(
                "google_cloud_tts.using_key",
                key=Path(str(credentials_path)).name,
            )
            return texttospeech.TextToSpeechClient(credentials=creds)

        # Fallback: ADC (GOOGLE_APPLICATION_CREDENTIALS or metadata server)
        return texttospeech.TextToSpeechClient()


# ── ElevenLabs provider ─────────────────────────────────────────────────────


class ElevenLabsProvider(TTSProvider):
    """ElevenLabs TTS with round-robin API-key rotation.

    The ``api_key`` kwarg is injected per-call by ``TTSService``.
    If absent, the ElevenLabs SDK falls back to the
    ``ELEVEN_API_KEY`` environment variable.

    Preset kwargs (from ``tts_presets.json``):
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
        stability = float(kwargs.get("stability", 0.5))  # type: ignore[arg-type]
        similarity_boost = float(kwargs.get("similarity_boost", 0.75))  # type: ignore[arg-type]
        style = float(kwargs.get("style", 0.0))  # type: ignore[arg-type]
        # Injected by TTSService; falls back to a conservative default.
        timeout_sec = float(kwargs.get("timeout", 120.0))  # type: ignore[arg-type]

        client = (
            ElevenLabs(api_key=api_key, timeout=timeout_sec)
            if api_key
            else ElevenLabs(timeout=timeout_sec)
        )

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

    _DEFAULT_PRESET: dict[str, Any] = {"provider": "gtts", "lang": "vi"}

    def __init__(self, path: Path) -> None:
        self._presets: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                self._presets = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.error(
                    "tts.preset_load_failed",
                    path=str(path),
                    error=str(exc),
                    fallback="all presets will use default gtts provider",
                )

    def get(self, ref: str) -> dict[str, Any]:
        return self._presets.get(ref, self._DEFAULT_PRESET)


# ── KeyRing helpers (loading) ────────────────────────────────────────────────


def _load_google_key_ring(settings: Settings) -> KeyRing[Path] | None:
    """Try to load Google Cloud service-account keys from disk.

    Returns ``None`` (with a warning) if the directory is missing or
    contains no JSON files – the provider will fall back to ADC.
    """
    keys_dir = settings.google_tts_keys_dir
    try:
        return KeyRing.from_json_dir(keys_dir)
    except FileNotFoundError:
        log.warning(
            "tts.google_key_ring_unavailable",
            directory=str(keys_dir),
            hint="Place service-account JSON files in the directory "
            "or set MAKER8_GOOGLE_APPLICATION_CREDENTIALS for a single key.",
        )
        return None


def _load_elevenlabs_key_ring(settings: Settings) -> KeyRing[str] | None:
    """Try to load ElevenLabs API keys from disk.

    Returns ``None`` if the directory is missing or empty – the provider
    will fall back to ``MAKER8_ELEVENLABS_API_KEY``.
    """
    keys_dir = settings.elevenlabs_keys_dir
    try:
        return KeyRing.from_text_dir(keys_dir)
    except (FileNotFoundError, ValueError):
        log.warning(
            "tts.elevenlabs_key_ring_unavailable",
            directory=str(keys_dir),
            hint="Place .txt/.key files (one API key each) in the directory "
            "or set MAKER8_ELEVENLABS_API_KEY for a single key.",
        )
        return None


# ── Façade ───────────────────────────────────────────────────────────────────


class TTSService:
    """High-level entry point used by the TTS pipeline stage.

    On construction the service loads all available key rings.  Each call
    to ``next_google_credentials()`` / ``next_elevenlabs_key()`` advances
    the ring by one position so that consecutive videos use different
    credentials (round-robin).

    Call hierarchy::

        TTSStage.execute()
          ├─ tts_service.next_google_credentials()   # once per video
          ├─ tts_service.next_elevenlabs_key()        # once per video
          └─ for scene in scenes:
                tts_service.synthesize(…, google_credentials_path=…)
    """

    _PROVIDERS: dict[str, type[TTSProvider]] = {
        "gtts": GTTSProvider,
        "google_cloud": GoogleCloudTTSProvider,
        "elevenlabs": ElevenLabsProvider,
    }

    def __init__(self, settings: Settings) -> None:
        self._preset_store = PresetStore(settings.tts_presets_path)
        self._default_provider = settings.tts_provider
        self._settings = settings

        # ── Load key rings (best-effort) ─────────────────────────────
        self._google_ring = _load_google_key_ring(settings)
        self._elevenlabs_ring = _load_elevenlabs_key_ring(settings)

        log.info(
            "tts_service.ready",
            default_provider=self._default_provider,
            google_keys=self._google_ring.size if self._google_ring else 0,
            elevenlabs_keys=(
                self._elevenlabs_ring.size if self._elevenlabs_ring else 0
            ),
        )

    # ── Per-video rotation ───────────────────────────────────────────

    def next_google_credentials(self) -> Path | None:
        """Advance the Google Cloud key ring and return a credentials path.

        Returns ``None`` when no key ring is loaded (provider uses ADC).
        """
        if self._google_ring:
            return self._google_ring.next()
        return None

    def next_elevenlabs_key(self) -> str:
        """Advance the ElevenLabs key ring and return an API key.

        Falls back to the single key from ``Settings`` when no ring is
        loaded.
        """
        if self._elevenlabs_ring:
            return self._elevenlabs_ring.next()
        return self._settings.elevenlabs_api_key

    # ── Per-scene synthesis ──────────────────────────────────────────

    def synthesize(
        self,
        text: str,
        lang: str,
        preset_ref: str,
        output_path: Path,
        *,
        google_credentials_path: Path | None = None,
        elevenlabs_api_key: str | None = None,
    ) -> SynthesisResult:
        """Synthesize *text* and write the audio to *output_path*.

        The optional ``google_credentials_path`` / ``elevenlabs_api_key``
        parameters allow the TTS stage to pin a single credential for all
        scenes of one video (round-robin per video, not per scene).
        """
        preset = dict(self._preset_store.get(preset_ref))
        provider_name = preset.pop("provider", self._default_provider)

        provider_cls = self._PROVIDERS.get(provider_name)
        if provider_cls is None:
            raise ValueError(f"Unknown TTS provider: {provider_name!r}")

        # ── Inject credentials ───────────────────────────────────────
        if provider_name == "google_cloud":
            if google_credentials_path is not None:
                preset["credentials_path"] = google_credentials_path
        elif provider_name == "elevenlabs":
            if elevenlabs_api_key:
                preset["api_key"] = elevenlabs_api_key
            elif "api_key" not in preset:
                preset["api_key"] = self._settings.elevenlabs_api_key

        # ── Inject timeout ───────────────────────────────────────────
        # Providers that support native timeouts (google_cloud, elevenlabs)
        # pop this value and pass it to the SDK.  gTTS ignores it but the
        # concurrent.futures backstop below still enforces the wall-clock
        # limit for *all* providers.
        timeout_sec = self._settings.tts_timeout_sec
        preset["timeout"] = timeout_sec

        provider = provider_cls()
        effective_lang = preset.pop("lang", lang)

        log.info(
            "tts.synthesize",
            provider=provider_name,
            lang=effective_lang,
            chars=len(text),
            timeout_sec=timeout_sec,
        )

        # ── Thread-based backstop ────────────────────────────────────
        # Bounds every provider (including gTTS which has no native
        # timeout API) to tts_timeout_sec wall-clock seconds.  Raises
        # TimeoutError which the TTS pipeline stage converts to a
        # retryable StageError.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
            _future = _pool.submit(
                provider.synthesize, text, effective_lang, output_path, **preset
            )
            try:
                return _future.result(timeout=timeout_sec)
            except concurrent.futures.TimeoutError:
                raise TimeoutError(
                    f"TTS synthesis timed out after {timeout_sec:.0f}s "
                    f"(provider={provider_name!r}, chars={len(text)})"
                )
