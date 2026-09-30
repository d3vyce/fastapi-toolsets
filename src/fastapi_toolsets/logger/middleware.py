"""ASGI middleware binding a request id to logs and logging each request."""

import logging
import re
import time
from collections.abc import Callable
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .context import request_log_context

__all__ = ["ACCESS_FIELDS", "ACCESS_LOGGER", "RequestLoggingMiddleware"]

ACCESS_LOGGER = "fastapi_toolsets.access"

# Fields of the access line that its message already shows.
ACCESS_FIELDS = ("method", "path", "route", "status", "duration_ms")

# An incoming id ends up in every log line; refuse anything that could forge one.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def _default_client_ip(scope: Scope) -> str | None:
    client = scope.get("client")
    return client[0] if client else None


class RequestLoggingMiddleware:
    """Bind a request id and client IP to every record, then log one access line.

    Args:
        app: The ASGI application to wrap.
        request_id_header: Response header carrying the request id. ``None``
            to omit it.
        trust_incoming_id: Reuse a valid id sent by the client in that header,
            for instance one set by a reverse proxy.
        client_ip: Returns the client IP for a scope. Defaults to the socket
            peer, which uvicorn's ``--proxy-headers`` sets behind a proxy.
        exclude: Returns ``True`` for requests to leave untouched, with the
            same signature as FastAPI's ``telemetry["exclude"]``.
        access_log: Log one line per request to ``ACCESS_LOGGER``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        request_id_header: str | None = "X-Request-ID",
        trust_incoming_id: bool = False,
        client_ip: Callable[[Scope], str | None] | None = None,
        exclude: Callable[[Scope], bool] | None = None,
        access_log: bool = True,
    ) -> None:
        self.app = app
        self.header = request_id_header
        self.trust_incoming_id = trust_incoming_id
        self.client_ip = client_ip or _default_client_ip
        self.exclude = exclude
        self.access_log = access_log
        self.logger = logging.getLogger(ACCESS_LOGGER)

    def _request_id(self, scope: Scope) -> str:
        if self.trust_incoming_id and self.header:
            incoming = Headers(scope=scope).get(self.header)
            if incoming and _VALID_REQUEST_ID.fullmatch(incoming):
                return incoming
        return uuid4().hex

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (self.exclude and self.exclude(scope)):
            await self.app(scope, receive, send)
            return

        request_id = self._request_id(scope)
        started = time.perf_counter()
        status = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                if self.header:
                    MutableHeaders(scope=message).append(self.header, request_id)
            await send(message)

        with request_log_context(
            request_id=request_id, client_ip=self.client_ip(scope)
        ):
            try:
                await self.app(scope, receive, send_with_request_id)
            finally:
                if self.access_log and self.logger.isEnabledFor(logging.INFO):
                    self._log(scope, status, time.perf_counter() - started)

    def _log(self, scope: Scope, status: int, elapsed: float) -> None:
        method = scope.get("method", "")
        path = scope.get("path", "")
        duration_ms = round(elapsed * 1000, 1)
        route = getattr(scope.get("route"), "path", None)
        values = (method, path, route, status, duration_ms)
        self.logger.info(
            "%s %s %s %sms",
            method,
            path,
            status,
            duration_ms,
            extra=dict(zip(ACCESS_FIELDS, values, strict=True)),
        )
