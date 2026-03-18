"""MultiAuth: combine multiple authentication sources into a single callable."""

import inspect
from typing import Any, cast

from fastapi import Request
from fastapi.security import SecurityScopes

from fastapi_toolsets.exceptions import UnauthorizedError

from ..abc import AuthSource


class MultiAuth:
    """Combine multiple authentication sources into a single callable.

    Sources are tried in order; the first one whose
    :meth:`~AuthSource.extract` returns a non-``None`` credential wins.
    Its :meth:`~AuthSource.authenticate` is called and the result returned.

    If a credential is found but the validator raises, the exception propagates
    immediately — the remaining sources are **not** tried. This prevents
    silent fallthrough on invalid credentials.

    If no source provides a credential,
    :class:`~fastapi_toolsets.exceptions.UnauthorizedError` is raised.

    The :meth:`~AuthSource.extract` method of each source performs only
    string matching (no I/O), so prefix-based dispatch is essentially free.

    Any :class:`~AuthSource` subclass — including user-defined ones — can be
    passed as a source.

    Args:
        *sources: Auth source instances to try in order.

    Example::

        user_bearer = BearerTokenAuth(verify_user, prefix="user_")
        org_bearer  = BearerTokenAuth(verify_org,  prefix="org_")
        cookie      = CookieAuth("session", verify_session)

        multi = MultiAuth(user_bearer, org_bearer, cookie)

        @app.get("/data")
        async def data_route(user = Security(multi)):
            return user

        # Apply a shared requirement to all sources at once
        @app.get("/admin")
        async def admin_route(user = Security(multi.require(role=Role.ADMIN))):
            return user
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
        """Return a new :class:`MultiAuth` with kwargs forwarded to each source.

        Calls ``.require(**kwargs)`` on every source that supports it. Sources
        that do not implement ``.require()`` (e.g. custom :class:`~AuthSource`
        subclasses) are passed through unchanged.

        New kwargs are merged over each source's existing kwargs — new values
        win on conflict::

            multi = MultiAuth(bearer, cookie)

            @app.get("/admin")
            async def admin(user = Security(multi.require(role=Role.ADMIN))):
                return user
        """
        new_sources = tuple(
            cast(Any, source).require(**kwargs)
            if hasattr(source, "require")
            else source
            for source in self._sources
        )
        return MultiAuth(*new_sources)
