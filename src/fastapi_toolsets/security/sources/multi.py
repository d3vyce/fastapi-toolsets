"""MultiAuth: combine multiple authentication sources into a single callable."""

import inspect
from typing import Any, cast

from fastapi import Request
from fastapi.security import SecurityScopes

from fastapi_toolsets.exceptions import UnauthorizedError

from ..abc import AuthSource


class MultiAuth:
    """Combine multiple authentication sources into a single callable.

    Args:
        *sources: Auth source instances to try in order.
    """

    def __init__(self, *sources: AuthSource) -> None:
        self._sources = sources

        async def _call(
            request: Request,
            security_scopes: SecurityScopes,  # noqa: ARG001
            **kwargs: Any,  # noqa: ARG001  — absorbs scheme values injected by FastAPI
        ) -> Any:
            for source in self._sources:
                credential = await source.extract(request)
                if credential is not None:
                    return await source.authenticate(credential)
            raise UnauthorizedError()

        self._call_fn = _call

        # Build a merged signature that includes the security-scheme Depends()
        # parameters from every source so FastAPI registers them in OpenAPI docs.
        seen: set[str] = {"request", "security_scopes"}
        merged: list[inspect.Parameter] = [
            inspect.Parameter(
                "request",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=Request,
            ),
            inspect.Parameter(
                "security_scopes",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=SecurityScopes,
            ),
        ]
        for i, source in enumerate(sources):
            for name, param in inspect.signature(source).parameters.items():
                if name in seen:
                    continue
                merged.append(param.replace(name=f"_s{i}_{name}"))
                seen.add(name)
        self.__signature__ = inspect.Signature(merged, return_annotation=Any)

    async def __call__(self, **kwargs: Any) -> Any:
        return await self._call_fn(**kwargs)

    def require(self, **kwargs: Any) -> "MultiAuth":
        """Return a new :class:`MultiAuth` with kwargs forwarded to each source."""
        new_sources = tuple(
            cast(Any, source).require(**kwargs)
            if hasattr(source, "require")
            else source
            for source in self._sources
        )
        return MultiAuth(*new_sources)
