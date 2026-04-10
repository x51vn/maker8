"""Periodic self-updater for the yt-dlp binary.

Downloads official release binaries from GitHub, verifies checksums,
and atomically activates new versions via a ``current`` symlink.

Activation only happens when the worker is idle (no job in progress).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

import requests

from maker8.utils.logging import get_logger

if TYPE_CHECKING:
    from maker8.observability.state import WorkerState

log = get_logger(__name__)

_GITHUB_API = "https://api.github.com"

# Channel → GitHub owner/repo for release lookups.
_CHANNEL_REPOS: dict[str, str] = {
    "stable": "yt-dlp/yt-dlp",
    "nightly": "yt-dlp/yt-dlp-nightly-builds",
}

_BINARY_NAME = "yt-dlp_linux"  # official asset name for x86-64 Linux
_SHA256_SUFFIX = ".sha256sum"
_STATUS_FILENAME = "maker8_ytdlp_updater_status.json"


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class UpdaterConfig:
    """Immutable updater parameters built from ``Settings``."""

    enabled: bool = False
    channel: str = "stable"
    bin_dir: Path = field(default_factory=lambda: Path("/opt/maker8/bin/yt-dlp"))
    interval_sec: int = 21600  # 6 h
    download_timeout: int = 120
    verify_checksum: bool = True
    min_check_interval_sec: int = 300


@dataclass
class UpdaterStatus:
    """Persisted updater state for observability."""

    current_version: str = ""
    previous_version: str = ""
    channel: str = "stable"
    last_check_ts: float = 0.0
    last_success_ts: float = 0.0
    last_failure_ts: float = 0.0
    last_failure_reason: str = ""


# ── Updater service ──────────────────────────────────────────────────────────


class YtdlpUpdater:
    """Background service that keeps the managed yt-dlp binary up-to-date."""

    def __init__(
        self,
        config: UpdaterConfig,
        worker_state: WorkerState | None = None,
    ) -> None:
        self._cfg = config
        self._worker_state = worker_state
        self._status = UpdaterStatus(channel=config.channel)
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._status_path = Path(tempfile.gettempdir()) / _STATUS_FILENAME

        # Load persisted status if available
        self._load_status()

    # ── Public API ───────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background update loop in a daemon thread."""
        if not self._cfg.enabled:
            log.info("ytdlp_updater.disabled")
            return
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = Thread(
            target=self._run_loop,
            name="ytdlp-updater",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "ytdlp_updater.started",
            channel=self._cfg.channel,
            interval_sec=self._cfg.interval_sec,
        )

    def stop(self) -> None:
        """Signal the background loop to stop."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    @property
    def status(self) -> UpdaterStatus:
        with self._lock:
            return UpdaterStatus(**asdict(self._status))

    def check_now(self) -> bool:
        """Run a single update check synchronously. Returns True if updated."""
        return self._check_and_update()

    # ── Background loop ──────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Periodic check loop running in a daemon thread."""
        while not self._stop.is_set():
            try:
                self._check_and_update()
            except Exception:
                log.exception("ytdlp_updater.loop_error")

            # Wait for interval or until stopped
            self._stop.wait(timeout=self._cfg.interval_sec)

    # ── Core logic ───────────────────────────────────────────────────

    def _check_and_update(self) -> bool:
        """Check for a new release and activate it if found.

        Returns ``True`` if a new version was activated.
        """
        now = time.time()

        # Throttle: don't check more often than min_check_interval_sec
        with self._lock:
            if now - self._status.last_check_ts < self._cfg.min_check_interval_sec:
                return False

        repo = _CHANNEL_REPOS.get(self._cfg.channel)
        if not repo:
            log.error("ytdlp_updater.unknown_channel", channel=self._cfg.channel)
            return False

        log.info("ytdlp_updater.check.start", channel=self._cfg.channel, repo=repo)
        with self._lock:
            self._status.last_check_ts = now

        try:
            tag = self._fetch_latest_tag(repo)
        except Exception as exc:
            self._record_failure(f"Failed to fetch latest tag: {exc}")
            return False

        if not tag:
            log.info("ytdlp_updater.check.noop", reason="no_release_found")
            return False

        with self._lock:
            if tag == self._status.current_version:
                log.info("ytdlp_updater.check.noop", reason="already_current", version=tag)
                return False

        # Don't update while a job is running
        if self._worker_state and self._worker_state.is_busy:
            log.info("ytdlp_updater.activate.skipped_busy", version=tag)
            return False

        try:
            return self._download_and_activate(repo, tag)
        except Exception as exc:
            self._record_failure(f"Update to {tag} failed: {exc}")
            return False

    def _fetch_latest_tag(self, repo: str) -> str | None:
        """Fetch the latest release tag from GitHub API."""
        url = f"{_GITHUB_API}/repos/{repo}/releases/latest"
        resp = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("tag_name", "")

    def _download_and_activate(self, repo: str, tag: str) -> bool:
        """Download, verify, and atomically activate a new yt-dlp binary."""
        log.info("ytdlp_updater.download.start", version=tag)

        bin_dir = self._cfg.bin_dir
        bin_dir.mkdir(parents=True, exist_ok=True)

        version_dir = bin_dir / tag
        version_dir.mkdir(parents=True, exist_ok=True)
        binary_path = version_dir / "yt-dlp"

        # Download binary
        binary_url = f"https://github.com/{repo}/releases/download/{tag}/{_BINARY_NAME}"
        self._download_file(binary_url, binary_path)

        # Verify checksum
        if self._cfg.verify_checksum:
            sha_url = f"{binary_url}{_SHA256_SUFFIX}"
            if not self._verify_checksum(sha_url, binary_path):
                log.error("ytdlp_updater.verify.failed", version=tag)
                self._record_failure(f"Checksum verification failed for {tag}")
                # Clean up failed download
                binary_path.unlink(missing_ok=True)
                return False

        # Make executable
        binary_path.chmod(binary_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        # Atomic activation via symlink
        current_link = bin_dir / "current"
        tmp_link = bin_dir / ".current.tmp"
        tmp_link.unlink(missing_ok=True)
        os.symlink(binary_path, tmp_link)
        os.replace(tmp_link, current_link)

        with self._lock:
            self._status.previous_version = self._status.current_version
            self._status.current_version = tag
            self._status.last_success_ts = time.time()
        self._save_status()

        log.info(
            "ytdlp_updater.activate.success",
            version=tag,
            previous=self._status.previous_version,
            path=str(binary_path),
        )
        return True

    def _download_file(self, url: str, dest: Path) -> None:
        """Download a file from *url* to *dest*."""
        resp = requests.get(url, timeout=self._cfg.download_timeout, stream=True)
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)

    def _verify_checksum(self, sha_url: str, binary_path: Path) -> bool:
        """Download and verify the SHA-256 checksum for *binary_path*."""
        try:
            resp = requests.get(sha_url, timeout=30)
            resp.raise_for_status()
        except Exception:
            log.warning("ytdlp_updater.checksum_download_failed", url=sha_url)
            # If checksum file isn't available, skip verification
            return True

        # Format: "<hash>  <filename>\n"
        expected_hash = resp.text.strip().split()[0]
        actual_hash = hashlib.sha256(binary_path.read_bytes()).hexdigest()
        return actual_hash == expected_hash

    def _record_failure(self, reason: str) -> None:
        log.error("ytdlp_updater.failure", reason=reason)
        with self._lock:
            self._status.last_failure_ts = time.time()
            self._status.last_failure_reason = reason
        self._save_status()

    # ── Status persistence ───────────────────────────────────────────

    def _save_status(self) -> None:
        try:
            with self._lock:
                data = asdict(self._status)
            self._status_path.write_text(json.dumps(data, indent=2))
        except Exception:
            log.debug("ytdlp_updater.status_save_failed")

    def _load_status(self) -> None:
        try:
            if self._status_path.exists():
                data = json.loads(self._status_path.read_text())
                with self._lock:
                    for k, v in data.items():
                        if hasattr(self._status, k):
                            setattr(self._status, k, v)
        except Exception:
            log.debug("ytdlp_updater.status_load_failed")
