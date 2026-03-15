"""Observability helpers – timing, sanitization, truncation."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Generator

__all__ = [
    "Timer",
    "sanitize_url",
    "truncate_stderr",
]


class Timer:
    """Simple wall-clock timer for measuring stage/subprocess durations."""

    __slots__ = ("_start", "_end")

    def __init__(self) -> None:
        self._start: float = 0.0
        self._end: float = 0.0

    def start(self) -> Timer:
        self._start = time.monotonic()
        return self

    def stop(self) -> Timer:
        self._end = time.monotonic()
        return self

    @property
    def elapsed_sec(self) -> float:
        end = self._end if self._end else time.monotonic()
        return round(end - self._start, 3) if self._start else 0.0

    @property
    def elapsed_ms(self) -> float:
        return round(self.elapsed_sec * 1000, 1)


@contextmanager
def timed() -> Generator[Timer, None, None]:
    """Context manager that yields a running ``Timer``."""
    t = Timer().start()
    try:
        yield t
    finally:
        t.stop()


def sanitize_url(url: str, *, max_len: int = 200) -> str:
    """Strip query-string secrets and truncate long URLs."""
    if "?" in url:
        base, _ = url.split("?", 1)
        url = f"{base}?<redacted>"
    if len(url) > max_len:
        return url[:max_len] + "..."
    return url


def truncate_stderr(stderr: str | None, max_len: int = 500) -> str:
    """Return last *max_len* chars of stderr for log inclusion."""
    if not stderr:
        return "(no stderr)"
    stderr = stderr.strip()
    if len(stderr) <= max_len:
        return stderr
    return "..." + stderr[-max_len:]
