"""Console and JSON formatters that render the fields attached to a record."""

import json
import logging
from collections import ChainMap
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import Any

from .context import _log_context, _request_context, trace_fields
from .middleware import ACCESS_FIELDS, ACCESS_LOGGER

__all__ = ["ConsoleFormatter", "JsonFormatter"]

# Attributes every LogRecord carries. Anything else was added through
# ``extra`` or the context filter and is rendered as a field.
_RESERVED_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime"}

_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_RESET = "\033[0m"
_DIM = "\033[2m"

_ENCODER = json.JSONEncoder(default=str)


def _own_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RESERVED_ATTRS and not key.startswith("_")
    }


def _tracebacks(
    formatter: logging.Formatter, record: logging.LogRecord
) -> dict[str, str]:
    """Return the rendered exception and stack of a record, when it has them."""
    rendered = {}
    if record.exc_info:
        rendered["exception"] = formatter.formatException(record.exc_info)
    if record.stack_info:
        rendered["stack"] = formatter.formatStack(record.stack_info)
    return rendered


def record_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Return the fields added through ``extra`` or the context, and the trace ids."""
    # Trace ids are read here rather than set on the record by a filter: the
    # record is shared with the OpenTelemetry handler, which already links it
    # to the span and would export them again as attributes.
    return trace_fields() | _own_fields(record)


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON object.

    Args:
        service: Value of the ``service`` key. Omitted when ``None``.
    """

    def __init__(self, *, service: str | None = None) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, UTC).isoformat(
            timespec="milliseconds"
        )
        # Fields first so an extra never replaces one of the base keys.
        payload = record_fields(record)
        payload.update(
            timestamp=stamp.replace("+00:00", "Z"),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
        )
        if self.service is not None:
            payload["service"] = self.service
        payload.update(_tracebacks(self, record))
        return _ENCODER.encode(payload)


class ConsoleFormatter(logging.Formatter):
    """Render a record as one short line for reading in a terminal.

    Within a request, the fields bound for the whole request are replaced by
    a short request id tag and shown only on the access line.

    Args:
        colors: Colour the level and dim the logger name and fields.
        datefmt: ``strftime`` format of the timestamp, followed by milliseconds.
        request_id_length: Characters of the request id and other ids shown.
        hidden_fields: Fields left out per logger name. Defaults to the
            access line fields its message already shows.
        logger_names: Names displayed in place of logger names. Defaults to
            ``access`` for the access logger.
    """

    def __init__(
        self,
        *,
        colors: bool = False,
        datefmt: str = "%H:%M:%S",
        request_id_length: int = 8,
        hidden_fields: Mapping[str, Collection[str]] | None = None,
        logger_names: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(datefmt=datefmt)
        self.colors = colors
        self.request_id_length = request_id_length
        self.hidden_fields = (
            {ACCESS_LOGGER: ACCESS_FIELDS} if hidden_fields is None else hidden_fields
        )
        self.logger_names = (
            {ACCESS_LOGGER: "access"} if logger_names is None else logger_names
        )

    def _context_field(self, key: str, value: Any) -> str:
        # Short labels: ``client_ip`` becomes ``ip`` and ``*_id`` drop the suffix.
        label = "ip" if key == "client_ip" else key
        if key.endswith("_id"):
            label = key.removesuffix("_id")
            value = str(value)[: self.request_id_length]
        return f"{label}={value}"

    def format(self, record: logging.LogRecord) -> str:
        stamp = f"{self.formatTime(record, self.datefmt)}.{int(record.msecs):03d}"
        level = record.levelname.ljust(5)
        name = self.logger_names.get(record.name, record.name)

        # The record cannot tell bound fields from extras; a field whose value
        # matches the live context (or active span) is taken as bound.
        trace = trace_fields()
        bound: Mapping[str, Any] = ChainMap(trace, _log_context.get() or {})
        request = _request_context.get()
        deferred: Mapping[str, Any] = (
            ChainMap(trace, request) if request and record.name != ACCESS_LOGGER else {}
        )
        hidden = self.hidden_fields.get(record.name, ())
        fields = trace | _own_fields(record)
        request_id = fields.pop("request_id", None)
        tail: list[str] = []
        context: dict[str, Any] = {}
        for key, value in fields.items():
            if key in hidden:
                continue
            if (key, value) not in bound.items():
                tail.append(f"{key}={value}")
            elif (key, value) not in deferred.items():
                context[key] = value
        if request_id is not None:
            tail.append(f"[{str(request_id)[: self.request_id_length]}]")
        # The application's fields before the client IP bound by the middleware.
        for key in sorted(context, key=lambda key: key == "client_ip"):
            tail.append(self._context_field(key, context[key]))
        rest = " ".join(tail)

        if self.colors:
            level = f"{_LEVEL_COLORS.get(record.levelname, '')}{level}{_RESET}"
            name = f"{_DIM}{name}{_RESET}"
            rest = f"{_DIM}{rest}{_RESET}" if rest else ""
        line = f"{stamp} {level} {name}  {record.getMessage()}"
        if rest:
            line = f"{line} {rest}"
        return "\n".join([line, *_tracebacks(self, record).values()])
