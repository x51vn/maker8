"""Tests for TTSService.has_provider() semantics (XST-1051).

Verifies:
  - google_cloud returns True in env_file mode (ADC fallback)
  - google_cloud requires DB keys in credential_source=db mode
  - gtts always returns True (no credentials needed)
  - elevenlabs returns True only when a key ring or env-var key is present in env mode
  - elevenlabs requires DB keys in credential_source=db mode
  - elevenlabs returns False when neither key ring nor env-var is configured
  - The startup check in app.py emits a warning but does NOT kill the process
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from maker8.services.tts_client import TTSService

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_settings(
    provider: str = "gtts",
    elevenlabs_api_key: str = "",
    google_keys_dir: str = "/nonexistent/keys",
    elevenlabs_keys_dir: str = "/nonexistent/elevenlabs",
) -> MagicMock:
    s = MagicMock()
    s.tts_provider = provider
    s.tts_presets_path = Path("config/tts_presets.json")
    s.google_tts_keys_dir = Path(google_keys_dir)
    s.elevenlabs_keys_dir = Path(elevenlabs_keys_dir)
    s.elevenlabs_api_key = elevenlabs_api_key
    return s


def _make_tts_service(
    provider: str = "gtts",
    elevenlabs_api_key: str = "",
    google_ring: object | None = None,
    elevenlabs_ring: object | None = None,
) -> TTSService:
    """Construct a TTSService with mocked key-ring loading."""
    settings = _make_settings(provider=provider, elevenlabs_api_key=elevenlabs_api_key)
    with (
        patch("maker8.services.tts_client._load_google_key_ring", return_value=google_ring),
        patch("maker8.services.tts_client._load_elevenlabs_key_ring", return_value=elevenlabs_ring),
        patch("maker8.services.tts_client.PresetStore"),
    ):
        return TTSService(settings)


def _make_tts_service_db(
    provider: str = "gtts",
    elevenlabs_api_key: str = "",
    google_ring: object | None = None,
    elevenlabs_ring: object | None = None,
) -> TTSService:
    """Construct a DB-backed TTSService with mocked DB key-ring loading."""
    settings = _make_settings(provider=provider, elevenlabs_api_key=elevenlabs_api_key)
    settings.work_dir = Path("/tmp/maker8-tests")
    reader = MagicMock()
    with (
        patch("maker8.services.tts_client._load_google_key_ring_from_db", return_value=google_ring),
        patch(
            "maker8.services.tts_client._load_elevenlabs_key_ring_from_db",
            return_value=elevenlabs_ring,
        ),
    ):
        return TTSService(settings, credential_reader=reader)


# ── Tests ────────────────────────────────────────────────────────────────────


class TestHasProvider:
    def test_gtts_always_true(self) -> None:
        """gtts needs no credentials – must always return True."""
        svc = _make_tts_service(provider="gtts")
        assert svc.has_provider() is True

    def test_google_cloud_no_key_ring_returns_true(self) -> None:
        """google_cloud with no key ring still returns True (ADC fallback)."""
        svc = _make_tts_service(provider="google_cloud", google_ring=None)
        assert svc.has_provider() is True

    def test_google_cloud_with_key_ring_returns_true(self) -> None:
        """google_cloud with a loaded key ring returns True."""
        mock_ring = MagicMock()
        mock_ring.size = 2
        svc = _make_tts_service(provider="google_cloud", google_ring=mock_ring)
        assert svc.has_provider() is True

    def test_elevenlabs_with_ring_returns_true(self) -> None:
        """elevenlabs with a loaded key ring returns True."""
        mock_ring = MagicMock()
        mock_ring.size = 1
        svc = _make_tts_service(
            provider="elevenlabs",
            elevenlabs_ring=mock_ring,
            elevenlabs_api_key="",
        )
        assert svc.has_provider() is True

    def test_elevenlabs_with_env_key_returns_true(self) -> None:
        """elevenlabs with a single env-var API key returns True."""
        svc = _make_tts_service(
            provider="elevenlabs",
            elevenlabs_api_key="sk-test-key",
            elevenlabs_ring=None,
        )
        assert svc.has_provider() is True

    def test_elevenlabs_no_keys_no_env_returns_false(self) -> None:
        """elevenlabs with no key ring and no env-var key returns False."""
        svc = _make_tts_service(
            provider="elevenlabs",
            elevenlabs_api_key="",
            elevenlabs_ring=None,
        )
        assert svc.has_provider() is False

    def test_unknown_provider_returns_false(self) -> None:
        """An unrecognised provider string returns False (fail-safe)."""
        svc = _make_tts_service(provider="nonexistent_provider")
        assert svc.has_provider() is False

    def test_google_cloud_no_db_keys_returns_false_in_db_mode(self) -> None:
        """DB mode does not allow ADC fallback when no DB key exists."""
        svc = _make_tts_service_db(provider="google_cloud", google_ring=None)
        assert svc.has_provider() is False

    def test_elevenlabs_env_key_ignored_in_db_mode(self) -> None:
        """DB mode ignores MAKER8_ELEVENLABS_API_KEY fallback."""
        svc = _make_tts_service_db(
            provider="elevenlabs",
            elevenlabs_api_key="sk-env-should-be-ignored",
            elevenlabs_ring=None,
        )
        assert svc.has_provider() is False


class TestStartupNoHardKill:
    """Ensure the startup TTS check does not hard-kill the process."""

    def test_tts_startup_path_uses_warning_not_exit(self) -> None:
        """The TTS has_provider() branch must log.warning, not os._exit."""
        from pathlib import Path

        src = Path("src/maker8/app.py").read_text(encoding="utf-8")

        # Locate the TTS-specific block by finding lines around has_provider()
        lines = src.splitlines()
        has_provider_line = next(
            (idx for idx, line in enumerate(lines) if "has_provider" in line), None
        )
        assert has_provider_line is not None, "has_provider() call not found in app.py"

        # Inspect the 20 lines following has_provider() – must not contain os._exit
        window = lines[has_provider_line : has_provider_line + 20]
        exit_in_window = any("os._exit" in line for line in window)
        assert not exit_in_window, (
            "os._exit() found near has_provider() in app.py. Should use log.warning() per XST-1051."
        )
        # The window should contain 'warning' (log.warning call)
        warning_in_window = any("warning" in line.lower() for line in window)
        assert warning_in_window, "No log.warning() found near has_provider() in app.py."

    def test_google_cloud_no_key_ring_never_triggers_warning(self) -> None:
        """google_cloud with no key ring => has_provider() True => no warning path."""
        svc = _make_tts_service(provider="google_cloud", google_ring=None)
        # This must be True so the startup warning branch is never entered.
        assert svc.has_provider() is True
