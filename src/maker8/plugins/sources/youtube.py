"""YouTube / multi-site source connector powered by *yt-dlp*."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maker8.observability.helpers import Timer, sanitize_url, truncate_stderr
from maker8.observability.metrics import SUBPROCESS_DURATION, SUBPROCESS_FAILURES
from maker8.plugins.base import PluginManifest, ResolvedAssetPlan, SourceConnectorPlugin
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_DEFAULT_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

# ── Error classification ─────────────────────────────────────────────────────

# Patterns that indicate a deterministic failure (do not retry).
_NON_RETRYABLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"video unavailable", re.I), "YTDLP_VIDEO_UNAVAILABLE"),
    (re.compile(r"private video", re.I), "YTDLP_VIDEO_UNAVAILABLE"),
    (re.compile(r"this video is private", re.I), "YTDLP_VIDEO_UNAVAILABLE"),
    (re.compile(r"this video is unavailable", re.I), "YTDLP_VIDEO_UNAVAILABLE"),
    (re.compile(r"this video has been removed", re.I), "YTDLP_VIDEO_UNAVAILABLE"),
    (re.compile(r"requested format is not available", re.I), "YTDLP_FORMAT_UNAVAILABLE"),
    (re.compile(r"unsupported url", re.I), "YTDLP_UNSUPPORTED_URL"),
    (re.compile(r"sign in to confirm", re.I), "YTDLP_AUTH_REQUIRED"),
    (re.compile(r"login required", re.I), "YTDLP_AUTH_REQUIRED"),
    (re.compile(r"age.?restrict", re.I), "YTDLP_AUTH_REQUIRED"),
    (re.compile(r"members.only", re.I), "YTDLP_AUTH_REQUIRED"),
    (re.compile(r"join this channel", re.I), "YTDLP_AUTH_REQUIRED"),
    (re.compile(r"confirm your age", re.I), "YTDLP_AUTH_REQUIRED"),
    (re.compile(r"po.?token", re.I), "YTDLP_PO_TOKEN_REQUIRED"),
]

# Patterns that indicate a transient failure (retry makes sense).
_RETRYABLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"HTTP Error 429", re.I), "YTDLP_RATE_LIMITED"),
    (re.compile(r"HTTP Error 5\d\d", re.I), "YTDLP_SERVER_ERROR"),
    (re.compile(r"timed?\s*out", re.I), "YTDLP_NETWORK_FAILURE"),
    (re.compile(r"connection reset", re.I), "YTDLP_NETWORK_FAILURE"),
    (re.compile(r"ssl|tls|handshake", re.I), "YTDLP_NETWORK_FAILURE"),
    (re.compile(r"name.+resolution|dns", re.I), "YTDLP_NETWORK_FAILURE"),
    (re.compile(r"temporary failure", re.I), "YTDLP_NETWORK_FAILURE"),
    (re.compile(r"errno|eoferror|broken pipe", re.I), "YTDLP_NETWORK_FAILURE"),
]


def classify_ytdlp_stderr(stderr: str) -> tuple[str, bool]:
    """Classify a yt-dlp stderr into ``(error_code, retryable)``.

    Returns a specific code when a known pattern is matched,
    otherwise falls back to ``YTDLP_EXTRACTOR_FAILURE``.
    """
    for pattern, code in _NON_RETRYABLE_PATTERNS:
        if pattern.search(stderr):
            return code, False
    for pattern, code in _RETRYABLE_PATTERNS:
        if pattern.search(stderr):
            return code, True
    # Unknown failure — assume transient to avoid data loss.
    return "YTDLP_EXTRACTOR_FAILURE", True


def _extract_stderr_summary(stderr: str) -> str:
    """Extract the most actionable line from yt-dlp stderr."""
    if not stderr:
        return "(no stderr)"
    # yt-dlp often prints "ERROR: <message>" as the final important line.
    for line in reversed(stderr.strip().splitlines()):
        line = line.strip()
        if line.upper().startswith("ERROR:"):
            return line
    # Fallback: last non-empty line.
    lines = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else "(no stderr)"


# ── Structured error ─────────────────────────────────────────────────────────


class YtdlpError(Exception):
    """Structured error from a yt-dlp subprocess failure.

    Attributes:
        code:           Machine-readable error code (e.g. ``YTDLP_AUTH_REQUIRED``).
        retryable:      Whether the orchestrator should retry this operation.
        asset_id:       The asset that triggered the failure.
        stderr_summary: Truncated, actionable stderr excerpt.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        asset_id: str = "",
        stderr_summary: str = "",
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.asset_id = asset_id
        self.stderr_summary = stderr_summary
        super().__init__(message)


# ── Runtime config ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class YouTubeRuntimeConfig:
    """Connector-level config populated from ``Settings`` at bootstrap."""

    executable: str = "yt-dlp"
    cookies_file: str = ""
    cookies_from_browser: str = ""
    user_agent: str = ""
    extractor_args: str = ""
    verbose_on_failure: bool = True
    resolve_timeout_sec: int = 120
    download_timeout_sec: int = 600


# ── Command builder ──────────────────────────────────────────────────────────


def _build_base_cmd(cfg: YouTubeRuntimeConfig) -> list[str]:
    """Build the common yt-dlp command prefix from runtime config."""
    cmd: list[str] = [cfg.executable]
    if cfg.cookies_file:
        cmd += ["--cookies", cfg.cookies_file]
    elif cfg.cookies_from_browser:
        cmd += ["--cookies-from-browser", cfg.cookies_from_browser]
    if cfg.user_agent:
        cmd += ["--user-agent", cfg.user_agent]
    if cfg.extractor_args:
        cmd += ["--extractor-args", cfg.extractor_args]
    return cmd


def _build_resolve_cmd(
    cfg: YouTubeRuntimeConfig,
    fmt: str,
    url: str,
) -> list[str]:
    """Build the full yt-dlp resolve (metadata probe) command."""
    cmd = _build_base_cmd(cfg)
    cmd += ["--dump-json", "--no-download", "-f", fmt, url]
    return cmd


def _build_download_cmd(
    cfg: YouTubeRuntimeConfig,
    fmt: str,
    output_tpl: str,
    url: str,
) -> list[str]:
    """Build the full yt-dlp download command."""
    cmd = _build_base_cmd(cfg)
    cmd += ["-f", fmt, "-o", output_tpl, "--merge-output-format", "mp4", url]
    return cmd


# ── Connector ────────────────────────────────────────────────────────────────


class YouTubeSourceConnector(SourceConnectorPlugin):
    """Resolve and download YouTube (and yt-dlp-supported) sources."""

    def __init__(self, config: YouTubeRuntimeConfig | None = None) -> None:
        self._cfg = config or YouTubeRuntimeConfig()

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="source/youtube", version="2.0.0", deterministic=True)

    def schema(self) -> dict[str, Any]:
        return {
            "kind": "youtube",
            "url": {"type": "string"},
            "options": {
                "format": {"type": "string"},
                "max_duration_sec": {"type": "integer"},
            },
        }

    # ── helpers ──────────────────────────────────────────────────────

    def _log_config_summary(self) -> dict[str, Any]:
        """Safe runtime config summary (no secrets)."""
        return {
            "executable": self._cfg.executable,
            "cookies_enabled": bool(self._cfg.cookies_file or self._cfg.cookies_from_browser),
            "cookies_mode": (
                "file"
                if self._cfg.cookies_file
                else ("browser" if self._cfg.cookies_from_browser else "none")
            ),
            "user_agent_set": bool(self._cfg.user_agent),
            "extractor_args_set": bool(self._cfg.extractor_args),
        }

    def _handle_subprocess_error(
        self,
        exc: subprocess.CalledProcessError,
        asset_id: str,
        operation: str,
        fmt: str,
        timer: Timer,
    ) -> YtdlpError:
        """Convert CalledProcessError -> structured YtdlpError."""
        timer.stop()
        SUBPROCESS_FAILURES.labels(stage=operation, source_kind="youtube").inc()
        stderr_raw = exc.stderr or ""
        stderr_trunc = truncate_stderr(stderr_raw)
        summary = _extract_stderr_summary(stderr_raw)
        code, retryable = classify_ytdlp_stderr(stderr_raw)

        log.error(
            "subprocess.failure",
            asset_id=asset_id,
            command="yt-dlp",
            operation=operation,
            format_spec=fmt,
            returncode=exc.returncode,
            stderr=stderr_trunc,
            error_code=code,
            retryable=retryable,
            stderr_summary=summary,
            duration_ms=timer.elapsed_ms,
            **self._log_config_summary(),
        )

        return YtdlpError(
            code=code,
            message=(f"Failed to {operation} asset {asset_id}: yt-dlp failed: {summary}"),
            retryable=retryable,
            asset_id=asset_id,
            stderr_summary=summary,
        )

    def _handle_timeout(
        self,
        asset_id: str,
        operation: str,
        timeout_sec: int,
        timer: Timer,
    ) -> YtdlpError:
        """Convert TimeoutExpired -> structured retryable YtdlpError."""
        timer.stop()
        SUBPROCESS_FAILURES.labels(stage=operation, source_kind="youtube").inc()
        log.error(
            "subprocess.timeout",
            asset_id=asset_id,
            command="yt-dlp",
            operation=operation,
            timeout_sec=timeout_sec,
        )
        return YtdlpError(
            code="YTDLP_TIMEOUT",
            message=(f"yt-dlp {operation} timed out after {timeout_sec}s for asset {asset_id}"),
            retryable=True,
            asset_id=asset_id,
            stderr_summary=f"Timed out after {timeout_sec}s",
        )

    # ── Resolve ──────────────────────────────────────────────────────

    def resolve(self, asset_id: str, source: dict[str, Any]) -> ResolvedAssetPlan:
        url = source.get("url")
        if not url:
            raise ValueError(f"Asset {asset_id!r} has no 'url' in its source – cannot resolve.")
        options = source.get("options", {})
        raw_fmt = options.get("format")
        max_dur = options.get("max_duration_sec")

        # Normalize: None/missing -> default, empty string -> validation error
        if raw_fmt is None:
            fmt = _DEFAULT_FORMAT
        elif not raw_fmt.strip():
            raise ValueError(
                f"Empty yt-dlp format spec for asset {asset_id!r}. "
                "Provide a valid format string or omit to use the default."
            )
        else:
            fmt = raw_fmt

        safe_url = sanitize_url(url)
        timeout = self._cfg.resolve_timeout_sec

        log.info(
            "ytdlp.resolve.start",
            asset_id=asset_id,
            url=safe_url,
            format_spec=fmt,
            max_duration_sec=max_dur,
            timeout_sec=timeout,
            **self._log_config_summary(),
        )

        cmd = _build_resolve_cmd(self._cfg, fmt, url)
        timer = Timer().start()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
            timer.stop()
            SUBPROCESS_DURATION.labels(
                stage="resolve",
                source_kind="youtube",
            ).observe(timer.elapsed_sec)
            log.info(
                "subprocess.success",
                asset_id=asset_id,
                command="yt-dlp",
                operation="resolve",
                format_spec=fmt,
                duration_ms=timer.elapsed_ms,
            )
        except subprocess.CalledProcessError as exc:
            raise self._handle_subprocess_error(
                exc,
                asset_id,
                "resolve",
                fmt,
                timer,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise self._handle_timeout(
                asset_id,
                "resolve",
                timeout,
                timer,
            ) from exc

        info = json.loads(result.stdout)

        duration = info.get("duration")
        if max_dur and duration and duration > max_dur:
            raise ValueError(f"Video duration {duration}s exceeds max_duration_sec={max_dur}")

        return ResolvedAssetPlan(
            asset_id=asset_id,
            source_kind="youtube",
            url=url,
            filename=f"{asset_id}.mp4",
            expected_type="video",
            format_spec=fmt,
            metadata={
                "title": info.get("title"),
                "duration": duration,
                "ext": info.get("ext", "mp4"),
            },
        )

    # ── Download ─────────────────────────────────────────────────────

    def download(self, plan: ResolvedAssetPlan, dest_dir: Path) -> Path:
        fmt = plan.format_spec or _DEFAULT_FORMAT
        output_tpl = str(dest_dir / f"{plan.asset_id}.%(ext)s")
        timeout = self._cfg.download_timeout_sec

        safe_url = sanitize_url(plan.url)
        log.info(
            "ytdlp.download.start",
            asset_id=plan.asset_id,
            url=safe_url,
            format_spec=fmt,
        )

        cmd = _build_download_cmd(self._cfg, fmt, output_tpl, plan.url)
        timer = Timer().start()
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
            timer.stop()
            SUBPROCESS_DURATION.labels(
                stage="download",
                source_kind="youtube",
            ).observe(timer.elapsed_sec)
            log.info(
                "subprocess.success",
                asset_id=plan.asset_id,
                command="yt-dlp",
                operation="download",
                duration_ms=timer.elapsed_ms,
            )
        except subprocess.CalledProcessError as exc:
            raise self._handle_subprocess_error(
                exc,
                plan.asset_id,
                "download",
                fmt,
                timer,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise self._handle_timeout(
                plan.asset_id,
                "download",
                timeout,
                timer,
            ) from exc

        # yt-dlp may produce various extensions; prefer .mp4
        for ext in ("mp4", "mkv", "webm"):
            candidate = dest_dir / f"{plan.asset_id}.{ext}"
            if candidate.exists():
                return candidate

        raise FileNotFoundError(f"yt-dlp did not produce an output file for {plan.asset_id}")


# ── Startup probe ────────────────────────────────────────────────────────────


def probe_ytdlp(executable: str = "yt-dlp") -> str | None:
    """Return the yt-dlp version string or ``None`` if not callable."""
    resolved = shutil.which(executable) or executable
    try:
        out = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def resolve_ytdlp_path(
    explicit_path: str,
    bin_dir: Path,
) -> str:
    """Determine the effective yt-dlp executable path.

    Priority:
    1. Explicit ``MAKER8_YTDLP_PATH`` if set.
    2. Managed binary ``<bin_dir>/current`` if it exists and is executable.
    3. ``yt-dlp`` from PATH.
    """
    if explicit_path:
        return explicit_path

    managed = bin_dir / "current"
    if managed.exists() and managed.is_file():
        return str(managed)

    return "yt-dlp"
