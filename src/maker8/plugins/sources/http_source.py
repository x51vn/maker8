"""HTTP(S) direct-download source connector."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from maker8.observability.helpers import Timer, sanitize_url
from maker8.observability.metrics import DEPENDENCY_FAILURES, DOWNLOAD_BYTES
from maker8.plugins.base import PluginManifest, ResolvedAssetPlan, SourceConnectorPlugin
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_CHUNK = 64 * 1024  # 64 KiB stream chunks
_CONNECT_TIMEOUT = 30  # seconds
_READ_TIMEOUT = 600  # seconds
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB safety limit
_USER_AGENT = "Maker8-RenderWorker/1.0"


class HttpSourceConnector(SourceConnectorPlugin):
    """Directly download a file over HTTP/HTTPS."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(id="source/http", version="1.0.0", deterministic=True)

    def schema(self) -> dict[str, Any]:
        return {
            "kind": "http",
            "url": {"type": "string"},
        }

    # ── Resolve ──────────────────────────────────────────────────────

    def resolve(self, asset_id: str, source: dict[str, Any]) -> ResolvedAssetPlan:
        url = source["url"]
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or ".bin"
        guessed_type = mimetypes.guess_type(parsed.path)[0] or ""

        expected_type = "video"
        if guessed_type.startswith("image"):
            expected_type = "image"
        elif guessed_type.startswith("audio"):
            expected_type = "audio"

        return ResolvedAssetPlan(
            asset_id=asset_id,
            source_kind="http",
            url=url,
            filename=f"{asset_id}{suffix}",
            expected_type=expected_type,
        )

    # ── Download ─────────────────────────────────────────────────────

    def download(self, plan: ResolvedAssetPlan, dest_dir: Path) -> Path:
        dest = dest_dir / plan.filename
        safe_url = sanitize_url(plan.url)
        log.info(
            "http.download.start",
            asset_id=plan.asset_id,
            url=safe_url,
            expected_type=plan.expected_type,
        )

        timer = Timer().start()
        try:
            resp = requests.get(
                plan.url,
                stream=True,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            timer.stop()
            DEPENDENCY_FAILURES.labels(dependency="http").inc()
            log.error(
                "http.download.failure",
                asset_id=plan.asset_id,
                url=safe_url,
                error_type=type(exc).__name__,
                error_message=str(exc),
                duration_ms=timer.elapsed_ms,
            )
            raise

        content_type = resp.headers.get("content-type")
        content_length = resp.headers.get("content-length")
        log.info(
            "http.download.response",
            asset_id=plan.asset_id,
            url=safe_url,
            status_code=resp.status_code,
            content_type=content_type,
            content_length=content_length,
        )

        downloaded = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=_CHUNK):
                downloaded += len(chunk)
                if downloaded > _MAX_DOWNLOAD_BYTES:
                    resp.close()
                    dest.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"HTTP download exceeded {_MAX_DOWNLOAD_BYTES / (1024**3):.0f} GiB limit"
                    )
                fh.write(chunk)

        timer.stop()
        DOWNLOAD_BYTES.labels(source_kind="http").observe(downloaded)
        log.info(
            "http.download.success",
            asset_id=plan.asset_id,
            url=safe_url,
            size_bytes=downloaded,
            content_type=resp.headers.get("content-type"),
            duration_ms=timer.elapsed_ms,
        )
        return dest
