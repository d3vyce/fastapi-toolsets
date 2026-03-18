"""Abstract base class for authentication sources."""

import inspect
from abc import ABC, abstractmethod
from typing import Any, Callable

from fastapi import Request
from fastapi.security import SecurityScopes

from fastapi_toolsets.exceptions import UnauthorizedError


def _ensure_async(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap *fn* so it can always be awaited, caching the coroutine check at init time."""
    if inspect.iscoroutinefunction(fn):
        return fn

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


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
