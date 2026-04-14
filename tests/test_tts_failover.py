"""TTS synthesis failover tests.

Verifies that maker8 retries alternate credentials for provider-level failures
and falls back to gTTS when all premium-provider attempts fail.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maker8.services.tts_client import SynthesisResult, TTSService


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.tts_provider = "gtts"
    s.tts_presets_path = Path("config/tts_presets.json")
    s.google_tts_keys_dir = Path("/nonexistent/google")
    s.elevenlabs_keys_dir = Path("/nonexistent/elevenlabs")
    s.elevenlabs_api_key = ""
    s.tts_timeout_sec = 10.0
    s.work_dir = Path("/tmp/maker8-tests")
    return s


def _make_service(
    *,
    google_ring: object | None = None,
    elevenlabs_ring: object | None = None,
) -> TTSService:
    settings = _make_settings()
    with (
        patch("maker8.services.tts_client._load_google_key_ring", return_value=google_ring),
        patch("maker8.services.tts_client._load_elevenlabs_key_ring", return_value=elevenlabs_ring),
        patch("maker8.services.tts_client.PresetStore"),
    ):
        return TTSService(settings)


def test_google_cloud_retries_alternate_credential_before_giving_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_key = tmp_path / "bad.json"
    good_key = tmp_path / "good.json"
    bad_key.write_text("{}", encoding="utf-8")
    good_key.write_text("{}", encoding="utf-8")

    google_ring = MagicMock()
    google_ring.size = 1
    google_ring.next.side_effect = [good_key]

    service = _make_service(google_ring=google_ring)
    service._preset_store.get.return_value = {  # noqa: SLF001
        "provider": "google_cloud",
        "lang": "vi-VN",
    }

    attempts: list[str] = []

    class FakeGoogleProvider:
        def synthesize(
            self,
            text: str,
            lang: str,
            output_path: Path,
            **kwargs: object,
        ) -> SynthesisResult:
            cred = Path(str(kwargs.get("credentials_path", ""))).name
            attempts.append(cred)
            if cred == "bad.json":
                raise PermissionError("billing disabled")
            output_path.write_bytes(b"ok")
            return SynthesisResult(audio_path=output_path, duration_sec=1.23)

    class FakeGttsProvider:
        def synthesize(
            self,
            text: str,
            lang: str,
            output_path: Path,
            **kwargs: object,
        ) -> SynthesisResult:
            output_path.write_bytes(b"fallback")
            return SynthesisResult(audio_path=output_path, duration_sec=0.5)

    monkeypatch.setattr(
        TTSService,
        "_PROVIDER_FACTORIES",
        {
            "google_cloud": FakeGoogleProvider,
            "gtts": FakeGttsProvider,
        },
    )

    out = tmp_path / "scene.mp3"
    result = service.synthesize(
        "xin chao",
        "vi-VN",
        "tts:vi:default",
        out,
        google_credentials_path=bad_key,
    )

    assert result.audio_path == out
    assert result.duration_sec == 1.23
    assert attempts == ["bad.json", "good.json"]


def test_google_cloud_falls_back_to_gtts_when_all_credentials_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_key_1 = tmp_path / "bad1.json"
    bad_key_2 = tmp_path / "bad2.json"
    bad_key_1.write_text("{}", encoding="utf-8")
    bad_key_2.write_text("{}", encoding="utf-8")

    google_ring = MagicMock()
    google_ring.size = 1
    google_ring.next.side_effect = [bad_key_2]

    service = _make_service(google_ring=google_ring)
    service._preset_store.get.return_value = {  # noqa: SLF001
        "provider": "google_cloud",
        "lang": "vi-VN",
    }

    calls: dict[str, int] = {"google": 0, "gtts": 0}

    class AlwaysFailGoogleProvider:
        def synthesize(
            self,
            text: str,
            lang: str,
            output_path: Path,
            **kwargs: object,
        ) -> SynthesisResult:
            calls["google"] += 1
            raise RuntimeError("google unavailable")

    class SuccessGttsProvider:
        def synthesize(
            self,
            text: str,
            lang: str,
            output_path: Path,
            **kwargs: object,
        ) -> SynthesisResult:
            calls["gtts"] += 1
            output_path.write_bytes(b"gtts-ok")
            return SynthesisResult(audio_path=output_path, duration_sec=0.9)

    monkeypatch.setattr(
        TTSService,
        "_PROVIDER_FACTORIES",
        {
            "google_cloud": AlwaysFailGoogleProvider,
            "gtts": SuccessGttsProvider,
        },
    )

    out = tmp_path / "scene.mp3"
    result = service.synthesize(
        "xin chao",
        "vi-VN",
        "tts:vi:default",
        out,
        google_credentials_path=bad_key_1,
    )

    assert result.audio_path == out
    assert result.duration_sec == 0.9
    assert calls["google"] == 2  # initial + one alternate key
    assert calls["gtts"] == 1
