"""Abstract base class for authentication sources."""

import functools
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

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


def _accepts_scopes(fn: Callable[..., Any]) -> bool:
    """Return whether *fn* declares a ``scopes`` parameter."""
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    param = parameters.get("scopes")
    return param is not None and param.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )


def _reject_scopes_kwarg(kwargs: dict[str, Any]) -> None:
    """Reject ``scopes`` as a validator kwarg."""
    if "scopes" in kwargs:
        raise ValueError(
            "'scopes' is a reserved validator kwarg: security scopes declared "
            "on the route via Security(..., scopes=[...]) are injected "
            "automatically. Use a different keyword name."
        )


def _scope_kwargs(
    owner: object, accepts_scopes: bool, scopes: list[str]
) -> dict[str, Any]:
    """Return the ``scopes`` kwarg for a validator, failing closed when unsupported."""
    if accepts_scopes:
        return {"scopes": scopes}
    if scopes:
        raise RuntimeError(
            f"{type(owner).__name__} cannot enforce the security scopes "
            f"{scopes!r} declared on this route: its validator does not "
            "declare a 'scopes' parameter. Add one to the validator or "
            "remove scopes=... from Security()."
        )
    return {}


class AuthSource(ABC):
    """Abstract base class for authentication sources."""

    def __init__(self) -> None:
        """Set up the default FastAPI dependency signature."""
        source = self

        async def _call(
            request: Request,
            security_scopes: SecurityScopes,
        ) -> Any:
            credential = await source.extract(request)
            if credential is None:
                raise UnauthorizedError()
            return await source.authenticate_scoped(credential, security_scopes.scopes)

        self._call_fn: Callable[..., Any] = _call
        self.__signature__ = inspect.signature(_call)

    @abstractmethod
    async def extract(self, request: Request) -> str | None:
        """Extract the raw credential from the request without validating."""

    @abstractmethod
    async def authenticate(self, credential: str) -> Any:
        """Validate a credential and return the authenticated identity."""

    async def authenticate_scoped(self, credential: str, scopes: list[str]) -> Any:
        """Validate a credential, enforcing the scopes declared on the route."""
        if scopes:
            raise RuntimeError(
                f"{type(self).__name__} cannot enforce the security scopes "
                f"{scopes!r} declared on this route. Override "
                "authenticate_scoped() to support scopes or remove "
                "scopes=... from Security()."
            )
        return await self.authenticate(credential)

    async def __call__(self, **kwargs: Any) -> Any:
        """FastAPI dependency dispatch."""
        return await self._call_fn(**kwargs)
