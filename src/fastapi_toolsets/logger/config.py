"""Logging configuration for FastAPI applications and CLI tools."""

import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .context import ContextFilter
from .formatters import ConsoleFormatter, JsonFormatter

__all__ = [
    "DEFAULT_QUIET_LOGGERS",
    "LogLevel",
    "LogStyle",
    "LoggingConfig",
    "configure_logging",
]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogStyle = Literal["console", "json", "auto"]

DEFAULT_QUIET_LOGGERS = (
    "asyncio",
    "httpcore",
    "httpx",
    "urllib3",
)

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_OWNED = "_fastapi_toolsets_handler"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, kw_only=True)
class LoggingConfig:
    """Settings for [`configure_logging`][fastapi_toolsets.logger.configure_logging].

    Args:
        level: Root log level.
        style: ``"console"``, ``"json"``, or ``"auto"`` for console on a
            terminal and JSON otherwise.
        stream: Stream the console or JSON handler writes to.
        colors: Colour console output. ``None`` enables it on a terminal.
        datefmt: ``strftime`` format of console timestamps, followed by
            milliseconds. Use ``"%Y-%m-%d %H:%M:%S"`` to show the date.
        request_id_length: Characters of the request id and other ids shown
            on the console.
        service: ``service`` key of JSON records. Defaults to
            ``OTEL_SERVICE_NAME``.
        levels: Per-logger levels, applied last.
        quiet: Loggers capped at ``WARNING``.
        sql_echo: Log SQLAlchemy statements at ``INFO``.
        otel: Forward records to OpenTelemetry logs. ``"auto"`` does so when
            the ``otel`` extra is installed.
        otel_logger_provider: Provider to forward to. ``None`` uses the
            global one.
    """

    level: LogLevel | int = "INFO"
    style: LogStyle = "auto"
    stream: Literal["stderr", "stdout"] = "stderr"
    colors: bool | None = None
    datefmt: str = "%H:%M:%S"
    request_id_length: int = 8
    service: str | None = None
    levels: Mapping[str, LogLevel | int] = field(default_factory=dict)
    quiet: Sequence[str] = DEFAULT_QUIET_LOGGERS
    sql_echo: bool = False
    otel: bool | Literal["auto"] = "auto"
    otel_logger_provider: Any = None

    @classmethod
    def from_env(
        cls,
        prefix: str = "LOG_",
        environ: Mapping[str, str] | None = None,
        **defaults: Any,
    ) -> "LoggingConfig":
        """Build a config from environment variables.

        Reads ``LEVEL``, ``STYLE``, ``STREAM``, ``COLORS``, ``SERVICE``,
        ``SQL_ECHO`` and ``OTEL`` under ``prefix``.

        Args:
            prefix: Prefix of the variable names.
            environ: Mapping to read instead of ``os.environ``.
            **defaults: Values used when a variable is unset.
        """
        env = os.environ if environ is None else environ
        values = dict(defaults)
        for name, parse in _ENV_PARSERS.items():
            key = f"{prefix}{name.upper()}"
            if raw := env.get(key):
                try:
                    values[name] = parse(raw)
                except ValueError as exc:
                    raise ValueError(f"{key}: {exc}") from None
        return cls(**values)


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"expected a boolean, got {raw!r}")


def _parse_otel(raw: str) -> bool | Literal["auto"]:
    return "auto" if raw.strip().lower() == "auto" else _parse_bool(raw)


_ENV_PARSERS: dict[str, Callable[[str], Any]] = {
    "level": str.upper,
    "style": str.lower,
    "stream": str.lower,
    "colors": _parse_bool,
    "service": str,
    "sql_echo": _parse_bool,
    "otel": _parse_otel,
}


def owned_handlers() -> list[logging.Handler]:
    """Return the root handlers installed by ``configure_logging``."""
    return [h for h in logging.getLogger().handlers if getattr(h, _OWNED, False)]


def _build_formatter(config: LoggingConfig, stream: Any) -> logging.Formatter:
    is_tty = hasattr(stream, "isatty") and stream.isatty()
    style = config.style
    if style == "auto":
        style = "console" if is_tty else "json"
    if style == "json":
        service = config.service or os.environ.get("OTEL_SERVICE_NAME")
        return JsonFormatter(service=service)
    if style == "console":
        colors = is_tty if config.colors is None else config.colors
        return ConsoleFormatter(
            colors=colors,
            datefmt=config.datefmt,
            request_id_length=config.request_id_length,
        )
    raise ValueError(f"Unknown log style {style!r}")


def _build_otel_handler(config: LoggingConfig) -> logging.Handler | None:
    if config.otel is False:
        return None
    try:
        from .otel import create_otel_handler
    except ImportError:
        if config.otel is True:
            from .._imports import require_extra

            require_extra(package="opentelemetry-sdk", extra="otel")
        return None
    return create_otel_handler(config.otel_logger_provider)


def configure_logging(
    config: LoggingConfig | None = None, **overrides: Any
) -> logging.Logger:
    """Configure the root logger, uvicorn's loggers, and the OpenTelemetry bridge.

    Calling it again replaces the handlers it installed.

    Args:
        config: Settings to apply. Defaults to ``LoggingConfig()``.
        **overrides: Fields replacing those of ``config``.

    Returns:
        The root logger.

    Example:
        ```python
        from fastapi_toolsets.logger import LoggingConfig, configure_logging

        configure_logging(LoggingConfig.from_env(), level="DEBUG")
        ```
    """
    config = replace(config or LoggingConfig(), **overrides)

    stream = getattr(sys, config.stream)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_build_formatter(config, stream))
    handler.addFilter(ContextFilter())
    handlers: list[logging.Handler] = [handler]
    if (otel_handler := _build_otel_handler(config)) is not None:
        handlers.append(otel_handler)

    root = logging.getLogger()
    for existing in owned_handlers():
        root.removeHandler(existing)
        existing.close()
    for new in handlers:
        setattr(new, _OWNED, True)
        root.addHandler(new)
    root.setLevel(config.level)

    # Uvicorn installs its own handlers; route its records through the root.
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(logging.NOTSET)

    for name in config.quiet:
        logging.getLogger(name).setLevel(logging.WARNING)
    if config.sql_echo:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
    for name, level in config.levels.items():
        logging.getLogger(name).setLevel(level)

    return root
