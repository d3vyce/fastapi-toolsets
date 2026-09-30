"""Logging for FastAPI applications and CLI tools."""

import logging
import sys
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from fastapi import FastAPI
from starlette.types import Scope

from .config import (
    DEFAULT_PROPAGATE_LOGGERS,
    DEFAULT_QUIET_LOGGERS,
    LoggingConfig,
    LogLevel,
    LogStyle,
    configure_logging,
)
from .context import ContextFilter, bind_log_context, current_log_context, log_context
from .files import SharedRotatingFileHandler
from .formatters import ConsoleFormatter, JsonFormatter
from .middleware import ACCESS_FIELDS, ACCESS_LOGGER, RequestLoggingMiddleware

__all__ = [
    "ACCESS_FIELDS",
    "ACCESS_LOGGER",
    "DEFAULT_PROPAGATE_LOGGERS",
    "DEFAULT_QUIET_LOGGERS",
    "ConsoleFormatter",
    "ContextFilter",
    "JsonFormatter",
    "LogLevel",
    "LogStyle",
    "LoggingConfig",
    "RequestLoggingMiddleware",
    "SharedRotatingFileHandler",
    "bind_log_context",
    "configure_logging",
    "current_log_context",
    "get_logger",
    "init_logging",
    "log_context",
]


def init_logging(
    app: FastAPI,
    config: LoggingConfig | None = None,
    *,
    access_log: bool = True,
    request_id_header: str | None = "X-Request-ID",
    trust_incoming_id: bool = False,
    client_ip: Callable[[Scope], str | None] | None = None,
    exclude: Callable[[Scope], bool] | None = None,
) -> FastAPI:
    """Configure logging and add [`RequestLoggingMiddleware`][fastapi_toolsets.logger.RequestLoggingMiddleware] to an app.

    Call it after adding your other middleware so it wraps them.

    Args:
        app: The FastAPI application.
        config: Passed to [`configure_logging`][fastapi_toolsets.logger.configure_logging].
        access_log: Log one line per request in place of uvicorn's access log.
        request_id_header: Response header carrying the request id.
        trust_incoming_id: Reuse a valid request id sent by the client.
        client_ip: Returns the client IP for a scope.
        exclude: Returns ``True`` for requests to leave untouched.

    Returns:
        The same app.
    """
    configure_logging(replace(config or LoggingConfig(), access_log=access_log))
    app.add_middleware(
        RequestLoggingMiddleware,
        request_id_header=request_id_header,
        trust_incoming_id=trust_incoming_id,
        client_ip=client_ip,
        exclude=exclude,
        access_log=access_log,
    )
    return app


_SENTINEL: Any = object()


def get_logger(name: str | None = _SENTINEL) -> logging.Logger:
    """Return a logger with the given *name*.

    When called without arguments, the caller's ``__name__`` is used, so
    ``get_logger()`` in a module is equivalent to
    ``logging.getLogger(__name__)``. Pass ``None`` to get the root logger.

    Args:
        name: Logger name. Defaults to the caller's ``__name__``.

    Returns:
        A Logger instance.

    Example:
        ```python
        from fastapi_toolsets.logger import get_logger

        logger = get_logger()          # uses caller's __name__
        logger = get_logger("myapp")   # explicit name
        logger = get_logger(None)      # root logger
        ```
    """
    if name is _SENTINEL:
        name = sys._getframe(1).f_globals.get("__name__")
    return logging.getLogger(name)
