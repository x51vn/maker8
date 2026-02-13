"""Thread-safe round-robin key ring for TTS service account rotation.

``KeyRing`` loads credential files from a directory at startup and hands
out the *next* credential on every ``next()`` call.  The ring is cyclic:
after the last key it wraps back to the first.

Two concrete loaders are provided:

* **JSON files** – for Google Cloud service account keys (``*.json``).
* **Text files** – for ElevenLabs API keys (``*.txt`` / ``*.key``),
  one key per file.

Usage::

    ring = KeyRing.from_json_dir(Path("gg-tts-keys"))
    creds_path = ring.next()        # Path to the next JSON key file

    ring = KeyRing.from_text_dir(Path("elevenlabs-keys"))
    api_key   = ring.next()         # str – the raw API key

The module is intentionally **provider-agnostic** so it can be reused
for any credential rotation scenario in the future.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Generic, TypeVar

from maker8.utils.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


class KeyRing(Generic[T]):
    """Cyclic round-robin container for credentials of type ``T``.

    Parameters
    ----------
    keys:
        Non-empty list of credentials.
    labels:
        Human-readable labels (same length as *keys*) used in log messages.
        Typically the originating filename.
    """

    def __init__(self, keys: list[T], labels: list[str]) -> None:
        if not keys:
            raise ValueError("KeyRing requires at least one key")
        if len(keys) != len(labels):
            raise ValueError("keys and labels must have the same length")

        self._keys = keys
        self._labels = labels
        self._index = 0
        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────────────

    def next(self) -> T:
        """Return the next credential in round-robin order (thread-safe)."""
        with self._lock:
            key = self._keys[self._index]
            label = self._labels[self._index]
            self._index = (self._index + 1) % len(self._keys)
        log.debug("key_ring.next", label=label, total=len(self._keys))
        return key

    @property
    def size(self) -> int:
        """Number of keys in the ring."""
        return len(self._keys)

    @property
    def labels(self) -> list[str]:
        """Copy of the label list (for diagnostics / startup logging)."""
        return list(self._labels)

    # ── Factory helpers ──────────────────────────────────────────────

    @classmethod
    def from_json_dir(cls, directory: Path) -> KeyRing[Path]:
        """Load ``*.json`` file paths from *directory* (sorted by name).

        Returns a ``KeyRing[Path]`` where each element is an **absolute
        path** to a JSON credential file.  Sorting ensures deterministic
        ordering across restarts.
        """
        if not directory.is_dir():
            raise FileNotFoundError(
                f"Key directory does not exist: {directory}"
            )

        paths = sorted(directory.glob("*.json"))
        if not paths:
            raise FileNotFoundError(
                f"No *.json credential files found in {directory}"
            )

        labels = [p.name for p in paths]
        log.info(
            "key_ring.loaded_json",
            directory=str(directory),
            count=len(paths),
            files=labels,
        )
        return cls(keys=[p.resolve() for p in paths], labels=labels)  # type: ignore[arg-type, return-value]

    @classmethod
    def from_text_dir(cls, directory: Path) -> KeyRing[str]:
        """Load API keys from text files in *directory* (sorted by name).

        Each file must contain exactly one API key (leading/trailing
        whitespace is stripped).  Recognised extensions: ``.txt``, ``.key``.

        Returns a ``KeyRing[str]`` where each element is the raw key
        string.
        """
        if not directory.is_dir():
            raise FileNotFoundError(
                f"Key directory does not exist: {directory}"
            )

        key_files = sorted(
            p
            for p in directory.iterdir()
            if p.is_file() and p.suffix in {".txt", ".key"}
        )
        if not key_files:
            raise FileNotFoundError(
                f"No *.txt / *.key files found in {directory}"
            )

        keys: list[str] = []
        labels: list[str] = []
        for kf in key_files:
            raw = kf.read_text(encoding="utf-8").strip()
            if not raw:
                log.warning("key_ring.empty_file", file=kf.name)
                continue
            keys.append(raw)
            labels.append(kf.name)

        if not keys:
            raise ValueError(
                f"All key files in {directory} are empty"
            )

        log.info(
            "key_ring.loaded_text",
            directory=str(directory),
            count=len(keys),
            files=labels,
        )
        return cls(keys=keys, labels=labels)  # type: ignore[arg-type, return-value]
