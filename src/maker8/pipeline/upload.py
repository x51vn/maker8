"""UPLOAD_DROPBOX stage – upload video + manifest to Dropbox."""

from __future__ import annotations

from dropbox.exceptions import (
    ApiError,
    AuthError,
    BadInputError,
    InternalServerError,
    RateLimitError,
)

from maker8.models.common import RenderStage
from maker8.models.manifest import Manifest, ManifestDropbox
from maker8.observability.helpers import Timer
from maker8.observability.metrics import DEPENDENCY_FAILURES
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.retry import StageError
from maker8.services.dropbox_client import DropboxClient
from maker8.utils.logging import get_logger

log = get_logger(__name__)


class UploadDropboxStage(Stage):
    def __init__(self, dbx_client: DropboxClient) -> None:
        self._dbx = dbx_client

    @property
    def name(self) -> RenderStage:
        return RenderStage.UPLOAD_DROPBOX

    def execute(self, ctx: PipelineContext) -> None:
        if ctx.rendered_video is None or not ctx.rendered_video.exists():
            raise StageError(
                self.name, "NO_VIDEO",
                "No rendered video file to upload",
                retryable=False,
            )

        video_size = ctx.rendered_video.stat().st_size
        log.info(
            "upload.start",
            job_id=ctx.job_id,
            video_path=str(ctx.rendered_video),
            video_bytes=video_size,
        )

        timer = Timer().start()
        try:
            # ── Upload video ─────────────────────────────────────────
            video_remote = DropboxClient.build_remote_path(
                ctx.job_id, f"{ctx.job_id}.mp4"
            )
            ctx.dropbox_video_ref = self._dbx.upload(
                ctx.rendered_video, video_remote, mime="video/mp4"
            )
            log.info("upload.video.success", job_id=ctx.job_id, path=video_remote)

            # ── Build & upload manifest ──────────────────────────────
            manifest = self._build_manifest(ctx)
            manifest_path = ctx.output_dir / f"{ctx.job_id}.manifest.json"
            manifest_path.write_text(
                manifest.model_dump_json(indent=2, by_alias=True),
                encoding="utf-8",
            )

            manifest_remote = DropboxClient.build_remote_path(
                ctx.job_id, f"{ctx.job_id}.manifest.json"
            )
            ctx.dropbox_manifest_ref = self._dbx.upload(
                manifest_path, manifest_remote, mime="application/json"
            )
            timer.stop()
            log.info(
                "upload.success",
                job_id=ctx.job_id,
                video_path=video_remote,
                manifest_path=manifest_remote,
                upload_sec=timer.elapsed_sec,
            )

        except AuthError as exc:
            DEPENDENCY_FAILURES.labels(dependency="dropbox").inc()
            log.error(
                "upload.auth_error",
                job_id=ctx.job_id,
                request_id=exc.request_id,
                error=str(exc),
                error_summary=getattr(exc.error, "_tag", None),
            )
            raise StageError(
                self.name, "AUTH_FAILED",
                f"Dropbox authentication failed: {exc}",
                retryable=False,
            ) from exc

        except BadInputError as exc:
            DEPENDENCY_FAILURES.labels(dependency="dropbox").inc()
            log.error(
                "upload.bad_input_error",
                job_id=ctx.job_id,
                request_id=exc.request_id,
                error=str(exc),
            )
            raise StageError(
                self.name, "INVALID_CONFIG",
                f"Dropbox configuration error: {exc}",
                retryable=False,
            ) from exc

        except RateLimitError as exc:
            DEPENDENCY_FAILURES.labels(dependency="dropbox").inc()
            backoff = getattr(exc, "backoff", None)
            log.warning(
                "upload.rate_limited",
                job_id=ctx.job_id,
                request_id=exc.request_id,
                backoff_seconds=backoff,
                error=str(exc),
            )
            raise StageError(
                self.name, "RATE_LIMITED",
                f"Dropbox rate limited (retry after {backoff}s): {exc}",
                retryable=True,
            ) from exc

        except InternalServerError as exc:
            DEPENDENCY_FAILURES.labels(dependency="dropbox").inc()
            log.warning(
                "upload.server_error",
                job_id=ctx.job_id,
                request_id=exc.request_id,
                status_code=exc.status_code,
                error=str(exc),
            )
            raise StageError(
                self.name, "SERVER_ERROR",
                f"Dropbox server error ({exc.status_code}): {exc}",
                retryable=True,
            ) from exc

        except ApiError as exc:
            DEPENDENCY_FAILURES.labels(dependency="dropbox").inc()
            error_tag = getattr(exc.error, "_tag", None)
            log.error(
                "upload.api_error",
                job_id=ctx.job_id,
                request_id=exc.request_id,
                error_tag=error_tag,
                error=str(exc),
            )
            raise StageError(
                self.name, "API_ERROR",
                f"Dropbox API error ({error_tag}): {exc}",
                retryable=True,
            ) from exc

        except StageError:
            raise

        except Exception as exc:
            DEPENDENCY_FAILURES.labels(dependency="dropbox").inc()
            log.exception(
                "upload.unexpected_error",
                job_id=ctx.job_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise StageError(
                self.name, "UPLOAD_FAILED",
                f"Unexpected upload error: {exc}",
                retryable=True,
            ) from exc

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_manifest(ctx: PipelineContext) -> Manifest:
        from maker8.utils.versions import collect_engine_versions

        return Manifest(
            job_id=ctx.job_id,
            job_key=ctx.job_key,
            dropbox=ManifestDropbox(
                video=ctx.dropbox_video_ref,
            ),
            output_meta=ctx.output_meta,
            publish_targets=ctx.render_spec.publish.targets,
            engine_versions=collect_engine_versions(),
        )
