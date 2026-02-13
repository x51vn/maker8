"""Dropbox SDK wrapper – upload files and return ``DropboxFileRef``."""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from pathlib import Path

import dropbox
from dropbox.files import WriteMode

from maker8.config import Settings
from maker8.models.common import DropboxFileRef
from maker8.utils.hashing import dropbox_content_hash, sha256_file
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_UPLOAD_LIMIT = 150 * 1024 * 1024  # 150 MiB – Dropbox simple-upload cap
_SESSION_CHUNK = 8 * 1024 * 1024  # 8 MiB per session append


class DropboxClient:
    """Upload files to Dropbox and obtain ``DropboxFileRef`` objects."""

    def __init__(self, settings: Settings) -> None:
        self._dbx = dropbox.Dropbox(
            oauth2_refresh_token=settings.dropbox_refresh_token,
            app_key=settings.dropbox_app_key,
            app_secret=settings.dropbox_app_secret,
        )

    # ── Public API ───────────────────────────────────────────────────

    def upload(
        self,
        local_path: Path,
        remote_path: str,
        mime: str = "",
    ) -> DropboxFileRef:
        """Upload *local_path* to *remote_path* and return a full ref."""
        file_size = local_path.stat().st_size
        log.info(
            "dropbox.upload",
            local=str(local_path),
            remote=remote_path,
            size=file_size,
        )

        if file_size <= _UPLOAD_LIMIT:
            result = self._simple_upload(local_path, remote_path)
        else:
            result = self._session_upload(local_path, remote_path, file_size)

        return DropboxFileRef(
            path=result.path_display,
            file_id=result.id,
            rev=result.rev,
            content_hash=dropbox_content_hash(local_path),
            sha256=sha256_file(local_path),
            bytes_=file_size,
            mime=mime or mimetypes.guess_type(local_path.name)[0] or "",
        )

    @staticmethod
    def build_remote_path(job_id: str, filename: str) -> str:
        """``/renders/<yyyy>/<mm>/<dd>/<filename>``."""
        now = datetime.now(UTC)
        return f"/renders/{now:%Y}/{now:%m}/{now:%d}/{filename}"

    # ── Internal ─────────────────────────────────────────────────────

    def _simple_upload(self, local_path: Path, remote_path: str) -> dropbox.files.FileMetadata:
        data = local_path.read_bytes()
        return self._dbx.files_upload(data, remote_path, mode=WriteMode.overwrite)

    def _session_upload(
        self,
        local_path: Path,
        remote_path: str,
        file_size: int,
    ) -> dropbox.files.FileMetadata:
        with open(local_path, "rb") as fh:
            session = self._dbx.files_upload_session_start(fh.read(_SESSION_CHUNK))
            cursor = dropbox.files.UploadSessionCursor(
                session_id=session.session_id,
                offset=fh.tell(),
            )

            while fh.tell() < file_size:
                remaining = file_size - fh.tell()
                chunk = fh.read(min(_SESSION_CHUNK, remaining))

                if fh.tell() >= file_size:
                    commit = dropbox.files.CommitInfo(
                        path=remote_path, mode=WriteMode.overwrite
                    )
                    return self._dbx.files_upload_session_finish(chunk, cursor, commit)

                self._dbx.files_upload_session_append_v2(chunk, cursor)
                cursor.offset = fh.tell()

            # Final commit (edge-case: exact multiple of chunk size)
            commit = dropbox.files.CommitInfo(
                path=remote_path, mode=WriteMode.overwrite
            )
            return self._dbx.files_upload_session_finish(b"", cursor, commit)
