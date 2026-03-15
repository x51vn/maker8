"""YouTube / multi-site source connector powered by *yt-dlp*."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from maker8.observability.helpers import Timer, sanitize_url, truncate_stderr
from maker8.observability.metrics import SUBPROCESS_DURATION, SUBPROCESS_FAILURES
from maker8.plugins.base import PluginManifest, ResolvedAssetPlan, SourceConnectorPlugin
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_DEFAULT_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"


class YouTubeSourceConnector(SourceConnectorPlugin):
    """Resolve and download YouTube (and yt-dlp-supported) sources."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="source/youtube", version="1.0.0", deterministic=True)

    def schema(self) -> dict[str, Any]:
        return {
            "kind": "youtube",
            "url": {"type": "string"},
            "options": {
                "format": {"type": "string"},
                "max_duration_sec": {"type": "integer"},
            },
        }

    # ── Resolve ──────────────────────────────────────────────────────

    def resolve(self, asset_id: str, source: dict[str, Any]) -> ResolvedAssetPlan:
        url = source.get("url")
        if not url:
            raise ValueError(
                f"Asset {asset_id!r} has no 'url' in its source – cannot resolve."
            )
        options = source.get("options", {})
        fmt = options.get("format", _DEFAULT_FORMAT)
        max_dur = options.get("max_duration_sec")

        if fmt is None:
            raise ValueError(
                f"Invalid yt-dlp format spec: None (asset_id={asset_id!r}). "
                "Provide a valid format string or omit to use the default."
            )

        safe_url = sanitize_url(url)
        log.info(
            "ytdlp.resolve.start",
            asset_id=asset_id,
            url=safe_url,
            format_spec=fmt,
            max_duration_sec=max_dur,
            timeout_sec=120,
        )

        cmd = ["yt-dlp", "--dump-json", "--no-download", "-f", fmt, url]
        log.info(
            "subprocess.start",
            asset_id=asset_id,
            command="yt-dlp",
            operation="resolve",
            url=safe_url,
            format_spec=fmt,
        )
        timer = Timer().start()
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=120
            )
            timer.stop()
            SUBPROCESS_DURATION.labels(stage="resolve", source_kind="youtube").observe(
                timer.elapsed_sec
            )
            log.info(
                "subprocess.success",
                asset_id=asset_id,
                command="yt-dlp",
                operation="resolve",
                format_spec=fmt,
                duration_ms=timer.elapsed_ms,
            )
        except subprocess.CalledProcessError as exc:
            timer.stop()
            SUBPROCESS_FAILURES.labels(stage="resolve", source_kind="youtube").inc()
            log.error(
                "subprocess.failure",
                asset_id=asset_id,
                command="yt-dlp",
                operation="resolve",
                format_spec=fmt,
                returncode=exc.returncode,
                stderr=truncate_stderr(exc.stderr or ""),
                duration_ms=timer.elapsed_ms,
            )
            raise
        except subprocess.TimeoutExpired:
            timer.stop()
            SUBPROCESS_FAILURES.labels(stage="resolve", source_kind="youtube").inc()
            log.error(
                "subprocess.timeout",
                asset_id=asset_id,
                command="yt-dlp",
                operation="resolve",
                timeout_sec=120,
            )
            raise

        info = json.loads(result.stdout)

        duration = info.get("duration")
        if max_dur and duration and duration > max_dur:
            raise ValueError(
                f"Video duration {duration}s exceeds max_duration_sec={max_dur}"
            )

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

        safe_url = sanitize_url(plan.url)
        log.info(
            "ytdlp.download.start",
            asset_id=plan.asset_id,
            url=safe_url,
            format_spec=fmt,
        )

        cmd = [
            "yt-dlp",
            "-f", fmt,
            "-o", output_tpl,
            "--merge-output-format", "mp4",
            plan.url,
        ]
        log.info(
            "subprocess.start",
            asset_id=plan.asset_id,
            command="yt-dlp",
            operation="download",
            url=safe_url,
            format_spec=fmt,
        )
        timer = Timer().start()
        try:
            subprocess.run(cmd, check=True, timeout=600)
            timer.stop()
            SUBPROCESS_DURATION.labels(stage="download", source_kind="youtube").observe(
                timer.elapsed_sec
            )
            log.info(
                "subprocess.success",
                asset_id=plan.asset_id,
                command="yt-dlp",
                operation="download",
                duration_ms=timer.elapsed_ms,
            )
        except subprocess.CalledProcessError as exc:
            timer.stop()
            SUBPROCESS_FAILURES.labels(stage="download", source_kind="youtube").inc()
            log.error(
                "subprocess.failure",
                asset_id=plan.asset_id,
                command="yt-dlp",
                operation="download",
                returncode=exc.returncode,
                stderr=truncate_stderr(getattr(exc, 'stderr', None) or ""),
                duration_ms=timer.elapsed_ms,
            )
            raise
        except subprocess.TimeoutExpired:
            timer.stop()
            SUBPROCESS_FAILURES.labels(stage="download", source_kind="youtube").inc()
            log.error(
                "subprocess.timeout",
                asset_id=plan.asset_id,
                command="yt-dlp",
                operation="download",
                timeout_sec=600,
            )
            raise

        # yt-dlp may produce various extensions; prefer .mp4
        for ext in ("mp4", "mkv", "webm"):
            candidate = dest_dir / f"{plan.asset_id}.{ext}"
            if candidate.exists():
                return candidate

        raise FileNotFoundError(
            f"yt-dlp did not produce an output file for {plan.asset_id}"
        )
