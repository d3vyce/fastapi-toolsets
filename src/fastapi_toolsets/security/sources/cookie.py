"""Cookie-based authentication source."""

import base64
import hashlib
import hmac
import inspect
import json
import time
from typing import Annotated, Any, Callable

from fastapi import Depends, Request, Response
from fastapi.security import APIKeyCookie, SecurityScopes

from fastapi_toolsets.exceptions import UnauthorizedError

from ..abc import AuthSource, _ensure_async


class CookieAuth(AuthSource):
    """Cookie-based authentication source.

    Wraps :class:`fastapi.security.APIKeyCookie` for OpenAPI documentation.
    Optionally signs the cookie with HMAC-SHA256 to provide stateless, tamper-
    proof sessions without any database entry.

    Args:
        name: Cookie name.
        validator: Sync or async callable that receives the cookie value
            (plain, after signature verification when ``secret_key`` is set)
            and any extra keyword arguments, and returns the authenticated
            identity.
        secret_key: When provided, the cookie is HMAC-SHA256 signed.
            :meth:`set_cookie` embeds an expiry and signs the payload;
            :meth:`extract` verifies the signature and expiry before handing
            the plain value to the validator.  When ``None`` (default), the raw
            cookie value is passed to the validator as-is.
        ttl: Cookie lifetime in seconds (default 24 h).  Only used when
            ``secret_key`` is set.
        **kwargs: Extra keyword arguments forwarded to the validator on every
            call (e.g. ``role=Role.ADMIN``).
    """

    def __init__(
        self,
        name: str,
        validator: Callable[..., Any],
        *,
        secret_key: str | None = None,
        ttl: int = 86400,
        **kwargs: Any,
    ) -> None:
        self._name = name
        self._validator = _ensure_async(validator)
        self._secret_key = secret_key
        self._ttl = ttl
        self._kwargs = kwargs
        self._scheme = APIKeyCookie(name=name, auto_error=False)

        async def _call(
            security_scopes: SecurityScopes,  # noqa: ARG001
            value: Annotated[str | None, Depends(self._scheme)] = None,
        ) -> Any:
            if value is None:
                raise UnauthorizedError()
            plain = self._verify(value)
            return await self._validator(plain, **self._kwargs)

        self._call_fn = _call
        self.__signature__ = inspect.signature(_call)

    def _hmac(self, data: str) -> str:
        if self._secret_key is None:
            raise RuntimeError("_hmac called without secret_key configured")
        return hmac.new(
            self._secret_key.encode(), data.encode(), hashlib.sha256
        ).hexdigest()

    def _sign(self, value: str) -> str:
        data = base64.urlsafe_b64encode(
            json.dumps({"v": value, "exp": int(time.time()) + self._ttl}).encode()
        ).decode()
        return f"{data}.{self._hmac(data)}"

    def _verify(self, cookie_value: str) -> str:
        """Return the plain value, verifying HMAC + expiry when signed."""
        if not self._secret_key:
            return cookie_value

        try:
            data, sig = cookie_value.rsplit(".", 1)
        except ValueError:
            raise UnauthorizedError()

        if not hmac.compare_digest(self._hmac(data), sig):
            raise UnauthorizedError()

        try:
            payload = json.loads(base64.urlsafe_b64decode(data))
            value: str = payload["v"]
            exp: int = payload["exp"]
        except Exception:
            raise UnauthorizedError()

        if exp < int(time.time()):
            raise UnauthorizedError()

        return value

    async def extract(self, request: Request) -> str | None:
        return request.cookies.get(self._name)

    async def authenticate(self, credential: str) -> Any:
        plain = self._verify(credential)
        return await self._validator(plain, **self._kwargs)

    def require(self, **kwargs: Any) -> "CookieAuth":
        """Return a new instance with additional (or overriding) validator kwargs."""
        return CookieAuth(
            self._name,
            self._validator,
            secret_key=self._secret_key,
            ttl=self._ttl,
            **{**self._kwargs, **kwargs},
        )

    def set_cookie(self, response: Response, value: str) -> None:
        """Attach the cookie to *response*, signing it when ``secret_key`` is set."""
        cookie_value = self._sign(value) if self._secret_key else value
        response.set_cookie(
            self._name,
            cookie_value,
            httponly=True,
            samesite="lax",
            max_age=self._ttl,
        )

    def delete_cookie(self, response: Response) -> None:
        """Clear the session cookie (logout)."""
        response.delete_cookie(self._name, httponly=True, samesite="lax")
