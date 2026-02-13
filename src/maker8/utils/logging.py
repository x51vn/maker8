"""Structured logging via *structlog*."""

from __future__ import annotations

import logging

import structlog


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure ``structlog`` with the chosen renderer.

    Parameters
    ----------
    level:
        Root log level (``DEBUG``, ``INFO``, ``WARNING``, …).
    fmt:
        ``"json"`` for production or ``"console"`` for local dev.
    """
    renderer: structlog.types.Processor
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a *structlog* bound logger scoped to *name*."""
    return structlog.get_logger(name)
