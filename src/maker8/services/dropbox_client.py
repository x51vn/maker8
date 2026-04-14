"""Dropbox SDK wrapper – upload files and return ``DropboxFileRef``."""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from pathlib import Path

import dropbox
from dropbox.files import WriteMode

from maker8.config import Settings
from maker8.models.common import DropboxFileRef
from maker8.utils.hashing import sha256_and_dropbox_hash
from maker8.utils.logging import get_logger

try:
    from maker8.services.credential_reader import CredentialReader as _CredentialReader
except ImportError:  # pragma: no cover
    _CredentialReader = None  # type: ignore[assignment,misc]

log = get_logger(__name__)

_UPLOAD_LIMIT = 150 * 1024 * 1024  # 150 MiB – Dropbox simple-upload cap
_SESSION_CHUNK = 8 * 1024 * 1024  # 8 MiB per session append


class DropboxClient:
    """Upload files to Dropbox and obtain ``DropboxFileRef`` objects."""

    def __init__(
        self,
        settings: Settings,
        credential_reader: _CredentialReader | None = None,  # type: ignore[valid-type]
    ) -> None:
        # ── Resolve credentials ─────────────────────────────────────────
        if credential_reader is not None:
            creds = credential_reader.get_first_key_json("dropbox_oauth")
            if creds is None:
                raise RuntimeError(
                    "No active 'dropbox_oauth' key found in editor8 database. "
                    "Add one via the editor8 UI → Settings → API Keys with "
                    "secret_value = JSON {app_key, app_secret, refresh_token}."
                )
            refresh_token: str = str(creds.get("refresh_token", ""))
            app_key: str = str(creds.get("app_key", ""))
            app_secret: str = str(creds.get("app_secret", ""))
        else:
            refresh_token = settings.dropbox_refresh_token
            app_key = settings.dropbox_app_key
            app_secret = settings.dropbox_app_secret

        log.info(
            "dropbox.init",
            credential_source="db" if credential_reader is not None else "env_file",
            has_refresh_token=bool(refresh_token),
            has_app_key=bool(app_key),
            has_app_secret=bool(app_secret),
        )
        self._dbx = dropbox.Dropbox(
            oauth2_refresh_token=refresh_token,
            app_key=app_key,
            app_secret=app_secret,
            timeout=300,  # 5 minutes
        )

        # Validate credentials at startup - fail hard if auth is broken
        try:
            account = self._dbx.users_get_current_account()
            log.info(
                "dropbox.auth_validated",
                account_id=account.account_id,
            )
        except Exception as exc:
            log.error(
                "dropbox.auth_validation_failed",
                error_type=type(exc).__name__,
                error=str(exc),
                note="Dropbox upload will fail during renders. Check credentials.",
            )
            raise RuntimeError(
                f"Dropbox authentication failed: {exc}. "
                "Fix MAKER8_DROPBOX_REFRESH_TOKEN / "
                "MAKER8_DROPBOX_APP_KEY / MAKER8_DROPBOX_APP_SECRET "
                "(env_file mode) or update the 'dropbox_oauth' key in editor8 DB."
            ) from exc

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
            "dropbox.upload.start",
            local=str(local_path),
            remote=remote_path,
            size=file_size,
            size_mb=round(file_size / 1024 / 1024, 2),
            method="session" if file_size > _UPLOAD_LIMIT else "simple",
        )

        try:
            if file_size <= _UPLOAD_LIMIT:
                result = self._simple_upload(local_path, remote_path)
            else:
                result = self._session_upload(local_path, remote_path, file_size)

            log.info(
                "dropbox.upload.success",
                remote=remote_path,
                file_id=result.id,
                rev=result.rev,
            )

            sha256_hex, dbx_hash = sha256_and_dropbox_hash(local_path)
            return DropboxFileRef(
                path=result.path_display,
                file_id=result.id,
                rev=result.rev,
                content_hash=dbx_hash,
                sha256=sha256_hex,
                bytes_=file_size,
                mime=mime or mimetypes.guess_type(local_path.name)[0] or "",
            )
        except Exception as exc:
            log.error(
                "dropbox.upload.failed",
                local=str(local_path),
                remote=remote_path,
                error_type=type(exc).__name__,
                error=str(exc),
                request_id=getattr(exc, "request_id", None),
            )
            raise

    @staticmethod
    def build_remote_path(filename: str) -> str:
        """``/renders/<yyyy>/<mm>/<dd>/<filename>``."""
        now = datetime.now(UTC)
        return f"/renders/{now:%Y}/{now:%m}/{now:%d}/{filename}"

    # ── Internal ─────────────────────────────────────────────────────

    def _simple_upload(self, local_path: Path, remote_path: str) -> dropbox.files.FileMetadata:
        log.debug("dropbox.simple_upload", remote=remote_path)
        data = local_path.read_bytes()
        result = self._dbx.files_upload(data, remote_path, mode=WriteMode.overwrite)
        log.debug("dropbox.simple_upload.done", remote=remote_path)
        return result

    def _session_upload(
        self,
        local_path: Path,
        remote_path: str,
        file_size: int,
    ) -> dropbox.files.FileMetadata:
        log.info(
            "dropbox.session_upload.start",
            remote=remote_path,
            file_size=file_size,
            chunk_size=_SESSION_CHUNK,
        )

        with open(local_path, "rb") as fh:
            # Start session
            log.debug("dropbox.session_upload.starting_session")
            chunk = fh.read(_SESSION_CHUNK)
            session = self._dbx.files_upload_session_start(chunk)
            session_id = session.session_id
            cursor = dropbox.files.UploadSessionCursor(
                session_id=session_id,
                offset=fh.tell(),
            )
            log.info(
                "dropbox.session_upload.session_started",
                session_id=session_id,
                offset=cursor.offset,
            )

            try:
                chunk_count = 1
                while fh.tell() < file_size:
                    remaining = file_size - fh.tell()
                    chunk = fh.read(min(_SESSION_CHUNK, remaining))
                    chunk_count += 1
                    progress_pct = round((fh.tell() / file_size) * 100, 1)

                    if fh.tell() >= file_size:
                        # Final chunk
                        log.info(
                            "dropbox.session_upload.finishing",
                            session_id=session_id,
                            progress_pct=progress_pct,
                        )
                        commit = dropbox.files.CommitInfo(
                            path=remote_path, mode=WriteMode.overwrite
                        )
                        result = self._dbx.files_upload_session_finish(chunk, cursor, commit)
                        log.info(
                            "dropbox.session_upload.finished",
                            remote=remote_path,
                            chunks=chunk_count,
                        )
                        return result

                    # Append chunk
                    log.debug(
                        "dropbox.session_upload.append",
                        session_id=session_id,
                        offset=cursor.offset,
                        progress_pct=progress_pct,
                        chunk=chunk_count,
                    )
                    self._dbx.files_upload_session_append_v2(chunk, cursor)
                    cursor.offset = fh.tell()

                # Final commit (edge-case: exact multiple of chunk size)
                log.info("dropbox.session_upload.final_commit", session_id=session_id)
                commit = dropbox.files.CommitInfo(path=remote_path, mode=WriteMode.overwrite)
                result = self._dbx.files_upload_session_finish(b"", cursor, commit)
                log.info(
                    "dropbox.session_upload.finished",
                    remote=remote_path,
                    chunks=chunk_count,
                )
                return result
            except Exception:
                # Best-effort abort of the dangling server-side session.
                try:
                    self._dbx.files_upload_session_finish(
                        b"",
                        cursor,
                        dropbox.files.CommitInfo(path=remote_path, mode=WriteMode.overwrite),
                    )
                except Exception:
                    log.debug("dropbox.session_upload.abort_failed", session_id=session_id)
                raise
