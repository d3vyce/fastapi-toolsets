"""Abstract base class for authentication sources."""

import inspect
from abc import ABC, abstractmethod
from typing import Any, Callable

from fastapi import Request
from fastapi.security import SecurityScopes

from fastapi_toolsets.exceptions import UnauthorizedError


async def _call_validator(
    validator: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Call *validator* with *args* and *kwargs*, awaiting it if it is a coroutine function."""
    if inspect.iscoroutinefunction(validator):
        return await validator(*args, **kwargs)
    return validator(*args, **kwargs)


class AuthSource(ABC):
    """Abstract base class for authentication sources."""

    def __init__(self) -> None:
        """Set up the default FastAPI dependency signature."""
        source = self

        async def _call(
            request: Request,
            security_scopes: SecurityScopes,  # noqa: ARG001
        ) -> Any:
            credential = await source.extract(request)
            if credential is None:
                raise UnauthorizedError()
            return await source.authenticate(credential)

        self._call_fn: Callable[..., Any] = _call
        self.__signature__ = inspect.signature(_call)

    @abstractmethod
    async def extract(self, request: Request) -> str | None:
        """Extract the raw credential from the request without validating."""

    @abstractmethod
    async def authenticate(self, credential: str) -> Any:
        """Validate a credential and return the authenticated identity."""

    async def __call__(self, **kwargs: Any) -> Any:
        """FastAPI dependency dispatch."""
        return await self._call_fn(**kwargs)
