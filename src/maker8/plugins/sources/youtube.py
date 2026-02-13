"""YouTube / multi-site source connector powered by *yt-dlp*."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from maker8.plugins.base import PluginManifest, ResolvedAssetPlan, SourceConnectorPlugin
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_DEFAULT_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"


class YouTubeSourceConnector(SourceConnectorPlugin):
    """Resolve and download YouTube (and yt-dlp-supported) sources."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="source/youtube", version="1.0.0", deterministic=True)

    def schema(self) -> dict:
        return {
            "kind": "youtube",
            "url": {"type": "string"},
            "options": {
                "format": {"type": "string"},
                "max_duration_sec": {"type": "integer"},
            },
        }

    # ── Resolve ──────────────────────────────────────────────────────

    def resolve(self, asset_id: str, source: dict) -> ResolvedAssetPlan:
        url = source["url"]
        options = source.get("options", {})
        fmt = options.get("format", _DEFAULT_FORMAT)
        max_dur = options.get("max_duration_sec")

        # Validate with yt-dlp --dump-json (no download)
        cmd = ["yt-dlp", "--dump-json", "--no-download", "-f", fmt, url]
        log.info("ytdlp.resolve", asset_id=asset_id, url=url)
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=120
        )
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

        cmd = [
            "yt-dlp",
            "-f", fmt,
            "-o", output_tpl,
            "--merge-output-format", "mp4",
            plan.url,
        ]
        log.info("ytdlp.download", asset_id=plan.asset_id)
        subprocess.run(cmd, check=True, timeout=600)

        # yt-dlp may produce various extensions; prefer .mp4
        for ext in ("mp4", "mkv", "webm"):
            candidate = dest_dir / f"{plan.asset_id}.{ext}"
            if candidate.exists():
                return candidate

        raise FileNotFoundError(
            f"yt-dlp did not produce an output file for {plan.asset_id}"
        )
