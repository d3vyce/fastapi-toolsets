"""Bearer token authentication source."""

import inspect
import secrets
from typing import Annotated, Any, Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, SecurityScopes

from fastapi_toolsets.exceptions import UnauthorizedError

from ..abc import AuthSource, _call_validator


class BearerTokenAuth(AuthSource):
    """Bearer token authentication source.

    Wraps :class:`fastapi.security.HTTPBearer` for OpenAPI documentation.
    The validator is called as ``await validator(credential, **kwargs)``
    where ``kwargs`` are the extra keyword arguments provided at instantiation.

    Args:
        validator: Sync or async callable that receives the credential and any
            extra keyword arguments, and returns the authenticated identity
            (e.g. a ``User`` model). Should raise
            :class:`~fastapi_toolsets.exceptions.UnauthorizedError` on failure.
        prefix: Optional token prefix (e.g. ``"user_"``). If set, only tokens
            whose value starts with this prefix are matched. The prefix is
            **kept** in the value passed to the validator — store and compare
            tokens with their prefix included. Use :meth:`generate_token` to
            create correctly-prefixed tokens. This enables multiple
            ``BearerTokenAuth`` instances in the same app (e.g. ``"user_"``
            for user tokens, ``"org_"`` for org tokens).
        **kwargs: Extra keyword arguments forwarded to the validator on every
            call (e.g. ``role=Role.ADMIN``).
    """

    def __init__(
        self,
        validator: Callable[..., Any],
        *,
        prefix: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._validator = validator
        self._prefix = prefix
        self._kwargs = kwargs
        self._scheme = HTTPBearer(auto_error=False)

        _scheme = self._scheme
        _validator = validator
        _kwargs = kwargs
        _prefix = prefix

        async def _call(
            security_scopes: SecurityScopes,  # noqa: ARG001
            credentials: Annotated[
                HTTPAuthorizationCredentials | None, Depends(_scheme)
            ] = None,
        ) -> Any:
            if credentials is None:
                raise UnauthorizedError()
            token = credentials.credentials
            if _prefix is not None and not token.startswith(_prefix):
                raise UnauthorizedError()
            return await _call_validator(_validator, token, **_kwargs)

        self._call_fn = _call
        self.__signature__ = inspect.signature(_call)

    async def extract(self, request: Any) -> str | None:
        """Extract the raw credential from the request without validating.

        Returns ``None`` if no ``Authorization: Bearer`` header is present,
        the token is empty, or the token does not match the configured prefix.
        The prefix is included in the returned value.
        """
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[7:]
        if not token:
            return None
        if self._prefix is not None and not token.startswith(self._prefix):
            return None
        return token

    async def authenticate(self, credential: str) -> Any:
        """Validate a credential and return the identity.

        Calls ``await validator(credential, **kwargs)`` where ``kwargs`` are
        the extra keyword arguments provided at instantiation.
        """
        return await _call_validator(self._validator, credential, **self._kwargs)

    def require(self, **kwargs: Any) -> "BearerTokenAuth":
        """Return a new instance with additional (or overriding) validator kwargs."""
        return BearerTokenAuth(
            self._validator,
            prefix=self._prefix,
            **{**self._kwargs, **kwargs},
        )

    def generate_token(self, nbytes: int = 32) -> str:
        """Generate a secure random token for this auth source.

        Returns a URL-safe random token. If a prefix is configured it is
        prepended — the returned value is what you store in your database
        and return to the client as-is.

        Args:
            nbytes: Number of random bytes before base64 encoding. The
                resulting string is ``ceil(nbytes * 4 / 3)`` characters
                (43 chars for the default 32 bytes). Defaults to 32.

        Returns:
            A ready-to-use token string (e.g. ``"user_Xk3..."``).
        """
        token = secrets.token_urlsafe(nbytes)
        if self._prefix is not None:
            return f"{self._prefix}{token}"
        return token
