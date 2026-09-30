"""Per-request log context and the filter that attaches it to records."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

try:
    from opentelemetry import trace as otel_trace
except ImportError:  # pragma: no cover
    otel_trace = None  # type: ignore[assignment]

__all__ = [
    "ContextFilter",
    "bind_log_context",
    "current_log_context",
    "log_context",
    "trace_fields",
]

_log_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "fastapi_toolsets_log_context", default=None
)
# The fields bound for the whole request, shared with ``_log_context`` there.
_request_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "fastapi_toolsets_request_context", default=None
)


def current_log_context() -> dict[str, Any]:
    """Return a copy of the fields bound to the current context."""
    return dict(_log_context.get() or {})


def bind_log_context(**values: Any) -> None:
    """Add fields to every record logged in the current scope.

    Inside a request handled by
    [`RequestLoggingMiddleware`][fastapi_toolsets.logger.RequestLoggingMiddleware],
    the fields stay bound until the request ends.

    Args:
        **values: Fields to bind. A ``None`` value is not emitted.
    """
    current = _log_context.get()
    if current is None:
        _log_context.set(dict(values))
    else:
        # Updated in place so a bind from a sync dependency, which FastAPI
        # runs on a copied context in the threadpool, reaches the request.
        current.update(values)


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    """Bind fields for the duration of the block, then restore the previous set.

    Args:
        **values: Fields to bind. A ``None`` value is not emitted.
    """
    token = _log_context.set({**(_log_context.get() or {}), **values})
    try:
        yield
    finally:
        _log_context.reset(token)


@contextmanager
def request_log_context(**values: Any) -> Iterator[None]:
    """Bind fields like ``log_context`` and mark them as the request's own.

    Args:
        **values: Fields to bind.
    """
    with log_context(**values):
        token = _request_context.set(_log_context.get())
        try:
            yield
        finally:
            _request_context.reset(token)


def request_fields() -> dict[str, Any]:
    """Return a copy of the fields bound for the whole current request."""
    return dict(_request_context.get() or {})


def trace_fields() -> dict[str, str]:
    """Return ``trace_id`` and ``span_id`` of the active OpenTelemetry span, if any."""
    if otel_trace is None:  # pragma: no cover
        return {}
    span_context = otel_trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }


class ContextFilter(logging.Filter):
    """Attach the fields bound with the context helpers to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_log_context.get() or {}).items():
            # Fields passed through ``extra`` win over the bound context.
            if value is not None and key not in record.__dict__:
                setattr(record, key, value)
        return True
