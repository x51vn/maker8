"""Hashing utilities used for job keys, content hashes, and integrity checks."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 8192
_DROPBOX_BLOCK = 4 * 1024 * 1024  # 4 MiB


def sha256_bytes(data: bytes) -> str:
    """Return hex SHA-256 digest of raw *data*."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return hex SHA-256 digest of a file read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def dropbox_content_hash(path: Path) -> str:
    """Compute Dropbox content-hash for a local file.

    Algorithm: split into 4 MiB blocks, SHA-256 each block,
    then SHA-256 the concatenation of those digests.
    """
    block_digests = b""
    with open(path, "rb") as fh:
        while True:
            block = fh.read(_DROPBOX_BLOCK)
            if not block:
                break
            block_digests += hashlib.sha256(block).digest()
    return hashlib.sha256(block_digests).hexdigest()


def sha256_and_dropbox_hash(path: Path) -> tuple[str, str]:
    """Compute SHA-256 and Dropbox content-hash in a single file read.

    Both digests are computed together so the file is only read once,
    which is ~2x faster than calling ``sha256_file`` + ``dropbox_content_hash``
    separately.  Returns ``(sha256_hex, dropbox_content_hash_hex)``.
    """
    h_sha256 = hashlib.sha256()
    block_digests = b""
    with open(path, "rb") as fh:
        while True:
            block = fh.read(_DROPBOX_BLOCK)  # 4 MiB slices satisfy both algorithms
            if not block:
                break
            h_sha256.update(block)
            block_digests += hashlib.sha256(block).digest()
    return h_sha256.hexdigest(), hashlib.sha256(block_digests).hexdigest()
