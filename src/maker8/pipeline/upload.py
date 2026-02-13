"""UPLOAD_DROPBOX stage – upload video + manifest to Dropbox."""

from __future__ import annotations

from maker8.models.common import RenderStage
from maker8.models.manifest import Manifest, ManifestDropbox
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

        try:
            # ── Upload video ─────────────────────────────────────────
            video_remote = DropboxClient.build_remote_path(
                ctx.job_id, f"{ctx.job_id}.mp4"
            )
            ctx.dropbox_video_ref = self._dbx.upload(
                ctx.rendered_video, video_remote, mime="video/mp4"
            )
            log.info("upload.video.ok", path=video_remote)

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
            log.info("upload.manifest.ok", path=manifest_remote)

        except StageError:
            raise
        except Exception as exc:
            raise StageError(
                self.name, "UPLOAD_FAILED",
                f"Dropbox upload failed: {exc}",
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
