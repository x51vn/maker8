"""Tests for the yt-dlp auto-updater service."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

# ── Stub prometheus_client before any maker8 module that uses metrics ────────
_prom_stub = ModuleType("prometheus_client")
for _attr in ("Counter", "Gauge", "Histogram", "Summary", "start_http_server"):
    setattr(_prom_stub, _attr, MagicMock())
sys.modules.setdefault("prometheus_client", _prom_stub)

from maker8.services.ytdlp_updater import UpdaterConfig, UpdaterStatus, YtdlpUpdater  # noqa: E402


class TestUpdaterConfig:
    """Verify UpdaterConfig defaults."""

    def test_defaults(self) -> None:
        cfg = UpdaterConfig()
        assert cfg.enabled is False
        assert cfg.channel == "stable"
        assert cfg.interval_sec == 21600
        assert cfg.verify_checksum is True


class TestUpdaterStatus:
    """Verify UpdaterStatus init and fields."""

    def test_defaults(self) -> None:
        st = UpdaterStatus()
        assert st.current_version == ""
        assert st.previous_version == ""
        assert st.last_check_ts == 0.0


class TestYtdlpUpdater:
    """Verify YtdlpUpdater behaviour with mocked network."""

    def _make_updater(self, tmp_path: Path, **overrides: object) -> YtdlpUpdater:
        cfg = UpdaterConfig(
            enabled=True,
            channel="stable",
            bin_dir=tmp_path / "bin",
            interval_sec=60,
            download_timeout=10,
            verify_checksum=False,
            min_check_interval_sec=0,
            **overrides,  # type: ignore[arg-type]
        )
        return YtdlpUpdater(config=cfg)

    def test_disabled_does_not_start(self) -> None:
        cfg = UpdaterConfig(enabled=False)
        updater = YtdlpUpdater(config=cfg)
        updater.start()
        assert updater._thread is None

    @patch("maker8.services.ytdlp_updater.requests.get")
    def test_check_now_noop_when_current(
        self,
        mock_get: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If already at latest, check_now returns False."""
        updater = self._make_updater(tmp_path)
        updater._status.current_version = "2025.01.15"

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"tag_name": "2025.01.15"}
        mock_get.return_value = resp

        assert updater.check_now() is False

    @patch("maker8.services.ytdlp_updater.requests.get")
    def test_check_now_downloads_new_version(
        self,
        mock_get: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If a new version is available, download and activate."""
        updater = self._make_updater(tmp_path)
        updater._status.current_version = "2025.01.01"

        # First call: release metadata
        release_resp = MagicMock()
        release_resp.status_code = 200
        release_resp.json.return_value = {"tag_name": "2025.01.15"}

        # Second call: binary download
        binary_resp = MagicMock()
        binary_resp.status_code = 200
        binary_resp.iter_content.return_value = [b"#!/bin/sh\necho fake"]

        mock_get.side_effect = [release_resp, binary_resp]

        assert updater.check_now() is True
        assert updater.status.current_version == "2025.01.15"
        assert updater.status.previous_version == "2025.01.01"

        # Verify symlink was created
        current_link = tmp_path / "bin" / "current"
        assert current_link.exists()

    @patch("maker8.services.ytdlp_updater.requests.get")
    def test_skips_when_worker_busy(
        self,
        mock_get: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Update should be skipped if worker is processing a job."""
        updater = self._make_updater(tmp_path)
        worker_state = MagicMock()
        worker_state.is_busy = True
        updater._worker_state = worker_state

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"tag_name": "2025.01.15"}
        mock_get.return_value = resp

        assert updater.check_now() is False

    def test_status_persistence(self, tmp_path: Path) -> None:
        """Status should persist to and load from file."""
        updater = self._make_updater(tmp_path)
        updater._status.current_version = "2025.01.15"
        updater._status.channel = "nightly"
        updater._save_status()

        # Verify file exists and content is valid JSON
        assert updater._status_path.exists()
        data = json.loads(updater._status_path.read_text())
        assert data["current_version"] == "2025.01.15"
        assert data["channel"] == "nightly"

        # Create a new updater from same path — should load persisted state
        updater2 = self._make_updater(tmp_path)
        updater2._status_path = updater._status_path
        updater2._load_status()
        assert updater2.status.current_version == "2025.01.15"

    @patch("maker8.services.ytdlp_updater.requests.get")
    def test_fetch_failure_records_error(
        self,
        mock_get: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If GitHub API fails, record failure but don't crash."""
        updater = self._make_updater(tmp_path)

        mock_get.side_effect = Exception("network error")

        assert updater.check_now() is False
        assert "network error" in updater.status.last_failure_reason
