"""API key header authentication source."""

import inspect
from typing import Annotated, Any, Callable

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, SecurityScopes

from fastapi_toolsets.exceptions import UnauthorizedError

from ..abc import AuthSource, _ensure_async


class APIKeyHeaderAuth(AuthSource):
    """API key header authentication source.

    Wraps :class:`fastapi.security.APIKeyHeader` for OpenAPI documentation.
    The validator is called as ``await validator(api_key, **kwargs)``
    where ``kwargs`` are the extra keyword arguments provided at instantiation.

    Args:
        name: HTTP header name that carries the API key (e.g. ``"X-API-Key"``).
        validator: Sync or async callable that receives the API key and any
            extra keyword arguments, and returns the authenticated identity.
            Should raise :class:`~fastapi_toolsets.exceptions.UnauthorizedError`
            on failure.
        **kwargs: Extra keyword arguments forwarded to the validator on every
            call (e.g. ``role=Role.ADMIN``).
    """

    def __init__(
        self,
        name: str,
        validator: Callable[..., Any],
        **kwargs: Any,
    ) -> None:
        self._name = name
        self._validator = _ensure_async(validator)
        self._kwargs = kwargs
        self._scheme = APIKeyHeader(name=name, auto_error=False)

        async def _call(
            security_scopes: SecurityScopes,  # noqa: ARG001
            api_key: Annotated[str | None, Depends(self._scheme)] = None,
        ) -> Any:
            if api_key is None:
                raise UnauthorizedError()
            return await self._validator(api_key, **self._kwargs)

        self._call_fn = _call
        self.__signature__ = inspect.signature(_call)

    async def extract(self, request: Request) -> str | None:
        """Extract the API key from the configured header."""
        return request.headers.get(self._name) or None

    async def authenticate(self, credential: str) -> Any:
        """Validate a credential and return the identity."""
        return await self._validator(credential, **self._kwargs)

    def require(self, **kwargs: Any) -> "APIKeyHeaderAuth":
        """Return a new instance with additional (or overriding) validator kwargs."""
        return APIKeyHeaderAuth(
            self._name,
            self._validator,
            **{**self._kwargs, **kwargs},
        )
