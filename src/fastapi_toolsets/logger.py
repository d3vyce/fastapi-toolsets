"""Logging configuration for FastAPI applications and CLI tools."""

import logging
import sys
from typing import Literal

__all__ = ["LogLevel", "configure_logging", "get_logger"]

DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def configure_logging(
    level: LogLevel | int = "INFO",
    fmt: str = DEFAULT_FORMAT,
    logger_name: str | None = None,
) -> logging.Logger:
    """Configure logging with a stdout handler and consistent format.

    Sets up a :class:`~logging.StreamHandler` writing to stdout with the
    given format and level.  Also configures the uvicorn loggers so that
    FastAPI access logs use the same format.

    Calling this function multiple times is safe -- existing handlers are
    replaced rather than duplicated.

    Args:
        level: Log level (e.g. ``"DEBUG"``, ``"INFO"``, or ``logging.DEBUG``).
        fmt: Log format string.  Defaults to
            ``"%(asctime)s - %(name)s - %(levelname)s - %(message)s"``.
        logger_name: Logger name to configure.  ``None`` (the default)
            configures the root logger so all loggers inherit the settings.

    Returns:
        The configured Logger instance.
    """
    formatter = logging.Formatter(fmt)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    logger = logging.getLogger(logger_name)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)

    for name in UVICORN_LOGGERS:
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.addHandler(handler)
        uv_logger.setLevel(level)

    return logger


_SENTINEL = object()


def get_logger(name: str | None = _SENTINEL) -> logging.Logger:  # type: ignore[assignment]
    """Return a logger with the given *name*.

    A thin convenience wrapper around :func:`logging.getLogger` that keeps
    logging imports consistent across the codebase.

    When called without arguments, the caller's ``__name__`` is used
    automatically, so ``get_logger()`` in a module is equivalent to
    ``logging.getLogger(__name__)``.  Pass ``None`` explicitly to get the
    root logger.

    Args:
        name: Logger name.  Defaults to the caller's ``__name__``.
            Pass ``None`` to get the root logger.

    Returns:
        A Logger instance.
    """
    if name is _SENTINEL:
        name = sys._getframe(1).f_globals.get("__name__")
    return logging.getLogger(name)
