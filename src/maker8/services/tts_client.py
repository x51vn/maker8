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
import contextlib
import json
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maker8.config import Settings
from maker8.services.key_ring import KeyRing
from maker8.utils.logging import get_logger

# Optional – only imported when credential_source == "db" so psycopg2 is not
# required for the default env_file mode.
try:
    from maker8.services.credential_reader import CredentialReader as _CredentialReader
except ImportError:  # pragma: no cover
    _CredentialReader = None  # type: ignore[assignment,misc]

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
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
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
    (a ``Path`` to a service-account JSON file).  If absent, the provider
    uses *Application Default Credentials* (ADC) only when ``allow_adc``
    is true (default).

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
        allow_adc = bool(kwargs.pop("allow_adc", True))
        client = self._build_client(credentials_path, allow_adc=allow_adc)

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
        audio_encoding = encoding_map.get(encoding_name, texttospeech.AudioEncoding.MP3)

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

    def __init__(self) -> None:
        self._client_cache: dict[str, object] = {}  # credentials_path → client

    def _build_client(
        self,
        credentials_path: object,
        *,
        allow_adc: bool = True,
    ) -> object:  # texttospeech.TextToSpeechClient
        """Return a cached TTS client, creating one if needed."""
        from google.cloud import texttospeech
        from google.oauth2 import service_account

        cache_key = str(credentials_path) if credentials_path else "__adc__"
        cached = self._client_cache.get(cache_key)
        if cached is not None:
            return cached

        if credentials_path and Path(str(credentials_path)).is_file():
            creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                str(credentials_path),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            log.debug(
                "google_cloud_tts.using_key",
                key=Path(str(credentials_path)).name,
            )
            client = texttospeech.TextToSpeechClient(credentials=creds)
            self._client_cache[cache_key] = client
            return client

        if not allow_adc:
            raise RuntimeError(
                "Google Cloud TTS credentials are missing. Add an active "
                "'google_cloud_service_account' key in editor8 UI."
            )

        # Fallback: ADC (GOOGLE_APPLICATION_CREDENTIALS or metadata server)
        client = texttospeech.TextToSpeechClient()
        self._client_cache[cache_key] = client
        return client


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

    def __init__(self) -> None:
        self._client_cache: dict[str, object] = {}  # api_key → ElevenLabs client

    def _get_client(self, api_key: str, timeout_sec: float) -> object:
        """Return a cached ElevenLabs client, creating one if needed."""
        from elevenlabs import ElevenLabs

        cache_key = api_key or "__default__"
        client = self._client_cache.get(cache_key)
        if client is None:
            client = (
                ElevenLabs(api_key=api_key, timeout=timeout_sec)
                if api_key
                else ElevenLabs(timeout=timeout_sec)
            )
            self._client_cache[cache_key] = client
        return client

    def synthesize(
        self,
        text: str,
        lang: str,
        output_path: Path,
        **kwargs: object,
    ) -> SynthesisResult:
        from elevenlabs import VoiceSettings

        api_key = str(kwargs.get("api_key", ""))
        voice_id = str(kwargs.get("voice_id", "21m00Tcm4TlvDq8ikWAM"))
        model_id = str(kwargs.get("model_id", "eleven_multilingual_v2"))
        stability = float(kwargs.get("stability", 0.5))  # type: ignore[arg-type]
        similarity_boost = float(kwargs.get("similarity_boost", 0.75))  # type: ignore[arg-type]
        style = float(kwargs.get("style", 0.0))  # type: ignore[arg-type]
        # Injected by TTSService; falls back to a conservative default.
        timeout_sec = float(kwargs.get("timeout", 120.0))  # type: ignore[arg-type]

        client = self._get_client(api_key, timeout_sec)

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


class _DbPresetStore:
    """Preset store backed by the editor8 DB via :class:`CredentialReader`."""

    _DEFAULT_PRESET: dict[str, Any] = {"provider": "gtts", "lang": "vi"}

    def __init__(self, reader: _CredentialReader) -> None:  # type: ignore[valid-type]
        self._reader = reader

    def get(self, ref: str) -> dict[str, Any]:
        preset = self._reader.get_tts_preset(ref)
        return preset if preset is not None else self._DEFAULT_PRESET


# ── KeyRing helpers (loading) ────────────────────────────────────────────────


def _load_google_key_ring_from_db(
    reader: _CredentialReader,  # type: ignore[valid-type]
    creds_dir: Path,
) -> KeyRing[Path] | None:
    """Build a ``KeyRing[Path]`` from Google Cloud JSON keys stored in the DB.

    Each ``secret_value`` is the raw JSON content of a service-account file.
    The content is written to temporary files under *creds_dir* so that
    :meth:`GoogleCloudTTSProvider._build_client` can load them by path.
    """
    keys = reader.get_keys("google_cloud_service_account")
    if not keys:
        log.warning(
            "tts.google_key_ring_db_empty",
            hint="Add a google_cloud_service_account key via the editor8 UI.",
        )
        return None

    creds_dir.mkdir(parents=True, exist_ok=True)
    # Clean stale credential files from previous runs.
    for stale in creds_dir.glob("google_sa_*.json"):
        with contextlib.suppress(OSError):
            stale.unlink()
    paths: list[Path] = []
    labels: list[str] = []
    for idx, json_content in enumerate(keys):
        dest = creds_dir / f"google_sa_{idx}.json"
        dest.write_text(json_content, encoding="utf-8")
        dest.chmod(0o600)
        paths.append(dest)
        try:
            info = json.loads(json_content)
            label = info.get("client_email", f"key_{idx}")
        except (json.JSONDecodeError, KeyError):
            label = f"key_{idx}"
        labels.append(label)

    return KeyRing(paths, labels)


def _load_elevenlabs_key_ring_from_db(
    reader: _CredentialReader,  # type: ignore[valid-type]
) -> KeyRing[str] | None:
    """Build a ``KeyRing[str]`` from ElevenLabs API keys stored in the DB."""
    keys = reader.get_keys("elevenlabs_api_key")
    if not keys:
        log.warning(
            "tts.elevenlabs_key_ring_db_empty",
            hint="Add an elevenlabs_api_key via the editor8 UI.",
        )
        return None
    labels = [f"el_key_{i}" for i in range(len(keys))]
    return KeyRing(keys, labels)


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

    _PROVIDER_FACTORIES: dict[str, type[TTSProvider]] = {
        "gtts": GTTSProvider,
        "google_cloud": GoogleCloudTTSProvider,
        "elevenlabs": ElevenLabsProvider,
    }

    def __init__(
        self,
        settings: Settings,
        credential_reader: _CredentialReader | None = None,  # type: ignore[valid-type]
    ) -> None:
        self._default_provider = settings.tts_provider
        self._settings = settings
        self._use_db = credential_reader is not None

        # Shared thread pool for timeout backstop (avoids per-call creation).
        self._timeout_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="tts-timeout"
        )

        # Provider instance cache (one per provider type, reuses connections).
        self._provider_instances: dict[str, TTSProvider] = {}

        # ── Preset store ──────────────────────────────────────────────
        if self._use_db:
            self._preset_store: PresetStore | _DbPresetStore = _DbPresetStore(credential_reader)  # type: ignore[arg-type]
        else:
            self._preset_store = PresetStore(settings.tts_presets_path)

        # ── Load key rings (best-effort) ─────────────────────────────
        if self._use_db:
            creds_dir = settings.work_dir / "credentials" / "google"
            self._google_ring = _load_google_key_ring_from_db(credential_reader, creds_dir)  # type: ignore[arg-type]
            self._elevenlabs_ring = _load_elevenlabs_key_ring_from_db(credential_reader)  # type: ignore[arg-type]
        else:
            self._google_ring = _load_google_key_ring(settings)
            self._elevenlabs_ring = _load_elevenlabs_key_ring(settings)

        log.info(
            "tts_service.ready",
            credential_source="db" if self._use_db else "env_file",
            default_provider=self._default_provider,
            google_keys=self._google_ring.size if self._google_ring else 0,
            elevenlabs_keys=(self._elevenlabs_ring.size if self._elevenlabs_ring else 0),
        )

    def has_provider(self) -> bool:
        """Return True if at least one TTS provider is available.

        Notes:
            - ``gtts``: always available; no credentials required.
            - ``google_cloud``:
              - DB mode: requires at least one active
                ``google_cloud_service_account`` key.
              - env/file mode: ADC fallback is allowed.
            - ``elevenlabs``:
              - DB mode: requires at least one active ``elevenlabs_api_key``.
              - env/file mode: key ring or single env-var key.
        """
        if self._default_provider == "gtts":
            return True  # gTTS uses no API key
        if self._default_provider == "google_cloud":
            # In DB mode we require explicit Google keys from editor8.
            if self._use_db:
                return bool(self._google_ring)
            # In legacy env/file mode, ADC can satisfy google_cloud.
            return True
        if self._default_provider == "elevenlabs":
            if self._use_db:
                return bool(self._elevenlabs_ring)
            return bool(self._elevenlabs_ring or self._settings.elevenlabs_api_key)
        return False

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
        if self._use_db:
            return ""
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

        # ── Inject credentials ───────────────────────────────────────
        if provider_name == "google_cloud":
            if google_credentials_path is not None:
                preset["credentials_path"] = google_credentials_path
            preset["allow_adc"] = not self._use_db
        elif provider_name == "elevenlabs":
            if elevenlabs_api_key:
                preset["api_key"] = elevenlabs_api_key
            elif "api_key" not in preset and not self._use_db:
                preset["api_key"] = self._settings.elevenlabs_api_key

        # ── Inject timeout ───────────────────────────────────────────
        # Providers that support native timeouts (google_cloud, elevenlabs)
        # pop this value and pass it to the SDK.  gTTS ignores it but the
        # concurrent.futures backstop below still enforces the wall-clock
        # limit for *all* providers.
        timeout_sec = self._settings.tts_timeout_sec
        preset["timeout"] = timeout_sec

        effective_lang = preset.pop("lang", lang)

        log.info(
            "tts.synthesize",
            provider=provider_name,
            lang=effective_lang,
            chars=len(text),
            timeout_sec=timeout_sec,
        )

        def _invoke(current_provider: str, current_preset: dict[str, Any]) -> SynthesisResult:
            provider_cls = self._PROVIDER_FACTORIES.get(current_provider)
            if provider_cls is None:
                raise ValueError(f"Unknown TTS provider: {current_provider!r}")
            # Re-use cached provider instance to preserve HTTP/gRPC connections.
            provider = self._provider_instances.get(current_provider)
            if provider is None:
                provider = provider_cls()
                self._provider_instances[current_provider] = provider

            # ── Thread-based backstop ────────────────────────────────
            # Bounds every provider (including gTTS which has no native
            # timeout API) to tts_timeout_sec wall-clock seconds.  Raises
            # TimeoutError which the TTS pipeline stage converts to
            # retryable/non-retryable warnings at scene level.
            _future = self._timeout_pool.submit(
                provider.synthesize,
                text,
                effective_lang,
                output_path,
                **current_preset,
            )
            try:
                return _future.result(timeout=timeout_sec)
            except concurrent.futures.TimeoutError as exc:
                raise TimeoutError(
                    f"TTS synthesis timed out after {timeout_sec:.0f}s "
                    f"(provider={current_provider!r}, chars={len(text)})"
                ) from exc

        def _retry_with_alternate_google_credentials(
            initial_error: Exception,
            base_preset: dict[str, Any],
        ) -> SynthesisResult | None:
            if not self._google_ring:
                return None
            attempted: set[str] = set()
            current = base_preset.get("credentials_path")
            if current is not None:
                attempted.add(str(current))

            for attempt in range(1, self._google_ring.size + 1):
                alt_cred = self._google_ring.next()
                alt_key = str(alt_cred)
                if alt_key in attempted:
                    continue
                attempted.add(alt_key)
                retry_preset = dict(base_preset)
                retry_preset["credentials_path"] = alt_cred
                try:
                    log.warning(
                        "tts.synthesize.retry_alternate_credential",
                        provider="google_cloud",
                        attempt=attempt,
                        reason_type=type(initial_error).__name__,
                    )
                    return _invoke("google_cloud", retry_preset)
                except Exception as exc:
                    initial_error = exc
                    log.warning(
                        "tts.synthesize.retry_alternate_credential_failed",
                        provider="google_cloud",
                        attempt=attempt,
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:500],
                    )
            return None

        def _retry_with_alternate_elevenlabs_keys(
            initial_error: Exception,
            base_preset: dict[str, Any],
        ) -> SynthesisResult | None:
            attempted: set[str] = set()
            current = str(base_preset.get("api_key", "")).strip()
            if current:
                attempted.add(current)

            if not self._elevenlabs_ring:
                return None

            for attempt in range(1, self._elevenlabs_ring.size + 1):
                alt_key = self._elevenlabs_ring.next().strip()
                if not alt_key or alt_key in attempted:
                    continue
                attempted.add(alt_key)
                retry_preset = dict(base_preset)
                retry_preset["api_key"] = alt_key
                try:
                    log.warning(
                        "tts.synthesize.retry_alternate_credential",
                        provider="elevenlabs",
                        attempt=attempt,
                        reason_type=type(initial_error).__name__,
                    )
                    return _invoke("elevenlabs", retry_preset)
                except Exception as exc:
                    initial_error = exc
                    log.warning(
                        "tts.synthesize.retry_alternate_credential_failed",
                        provider="elevenlabs",
                        attempt=attempt,
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:500],
                    )
            return None

        try:
            return _invoke(provider_name, dict(preset))
        except Exception as primary_exc:
            # If a pinned credential is dead (expired key, billing disabled,
            # revoked token), try other credentials from the same provider ring
            # before giving up this scene.
            if provider_name == "google_cloud":
                retry_result = _retry_with_alternate_google_credentials(primary_exc, dict(preset))
                if retry_result is not None:
                    return retry_result
            elif provider_name == "elevenlabs":
                retry_result = _retry_with_alternate_elevenlabs_keys(primary_exc, dict(preset))
                if retry_result is not None:
                    return retry_result

            # Last-resort fallback: preserve narration with gTTS when a premium
            # provider fails globally (credentials/quota/network).
            if provider_name != "gtts":
                log.warning(
                    "tts.synthesize.fallback_gtts",
                    failed_provider=provider_name,
                    error_type=type(primary_exc).__name__,
                    error_message=str(primary_exc)[:500],
                )
                fallback_preset: dict[str, Any] = {"timeout": timeout_sec}
                return _invoke("gtts", fallback_preset)

            raise
