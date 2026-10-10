"""Forward stdlib log records to OpenTelemetry logs."""

import logging
from typing import Any

from opentelemetry.instrumentation.logging.handler import LoggingHandler

__all__ = ["create_otel_handler"]


def _not_from_otel(record: logging.LogRecord) -> bool:
    # The SDK logs its own export errors; forwarding them could loop.
    return not record.name.startswith("opentelemetry")


def create_otel_handler(logger_provider: Any = None) -> logging.Handler:
    """Build a handler that emits records as OpenTelemetry logs.

    Args:
        logger_provider: Provider to emit to. ``None`` uses the global
            provider, including one set later, such as FastAPI's at startup.
    """
    handler = LoggingHandler(logger_provider=logger_provider)
    handler.addFilter(_not_from_otel)
    return handler
