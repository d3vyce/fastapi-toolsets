"""Tests for fastapi_toolsets.security."""

import pytest
from fastapi import FastAPI, Security
from fastapi.testclient import TestClient

from fastapi_toolsets.exceptions import UnauthorizedError, init_exceptions_handlers
from fastapi_toolsets.security import (
    APIKeyHeaderAuth,
    AuthSource,
    BearerTokenAuth,
    CookieAuth,
    MultiAuth,
)


def _app(*routes_setup_fns):
    """Build a minimal FastAPI test app with exception handlers."""
    app = FastAPI()
    init_exceptions_handlers(app)
    for fn in routes_setup_fns:
        fn(app)
    return app


VALID_TOKEN = "secret"
VALID_COOKIE = "session123"


async def simple_validator(credential: str) -> dict:
    if credential != VALID_TOKEN:
        raise UnauthorizedError()
    return {"user": "alice"}


async def role_validator(credential: str, *, role: str) -> dict:
    if credential != VALID_TOKEN:
        raise UnauthorizedError()
    return {"user": "alice", "role": role}


async def cookie_validator(value: str) -> dict:
    if value != VALID_COOKIE:
        raise UnauthorizedError()
    return {"session": value}


class TestBearerTokenAuth:
    def test_valid_token_returns_identity(self):
        bearer = BearerTokenAuth(simple_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(bearer)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert response.status_code == 200
        assert response.json() == {"user": "alice"}

    def test_missing_header_returns_401(self):
        bearer = BearerTokenAuth(simple_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(bearer)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me")
        assert response.status_code == 401

    def test_invalid_token_returns_401(self):
        bearer = BearerTokenAuth(simple_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(bearer)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401

    def test_kwargs_forwarded_to_validator(self):
        bearer = BearerTokenAuth(role_validator, role="admin")

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(bearer)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert response.status_code == 200
        assert response.json() == {"user": "alice", "role": "admin"}

    def test_prefix_matching_passes_full_token(self):
        """Token with matching prefix: full token (with prefix) is passed to validator."""
        received: list[str] = []

        async def capturing_validator(credential: str) -> dict:
            received.append(credential)
            return {"user": "alice"}

        bearer = BearerTokenAuth(capturing_validator, prefix="user_")

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(bearer)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"Authorization": "Bearer user_abc123"})
        assert response.status_code == 200
        # Prefix is kept — validator receives the full token as stored in DB
        assert received == ["user_abc123"]

    def test_prefix_mismatch_returns_401(self):
        bearer = BearerTokenAuth(simple_validator, prefix="user_")

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(bearer)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"Authorization": "Bearer org_abc123"})
        assert response.status_code == 401

    # --- extract() ---

    @pytest.mark.anyio
    async def test_extract_no_header(self):
        from starlette.requests import Request

        bearer = BearerTokenAuth(simple_validator)
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        request = Request(scope)
        assert await bearer.extract(request) is None

    @pytest.mark.anyio
    async def test_extract_empty_token(self):
        from starlette.requests import Request

        bearer = BearerTokenAuth(simple_validator)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer ")],
        }
        request = Request(scope)
        assert await bearer.extract(request) is None

    @pytest.mark.anyio
    async def test_extract_no_prefix(self):
        from starlette.requests import Request

        bearer = BearerTokenAuth(simple_validator)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer mytoken")],
        }
        request = Request(scope)
        assert await bearer.extract(request) == "mytoken"

    @pytest.mark.anyio
    async def test_extract_prefix_match(self):
        from starlette.requests import Request

        bearer = BearerTokenAuth(simple_validator, prefix="user_")
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer user_abc")],
        }
        request = Request(scope)
        assert await bearer.extract(request) == "user_abc"

    @pytest.mark.anyio
    async def test_extract_prefix_no_match(self):
        from starlette.requests import Request

        bearer = BearerTokenAuth(simple_validator, prefix="user_")
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer org_abc")],
        }
        request = Request(scope)
        assert await bearer.extract(request) is None

    # --- generate_token() ---

    def test_generate_token_no_prefix(self):
        bearer = BearerTokenAuth(simple_validator)
        token = bearer.generate_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_generate_token_with_prefix(self):
        bearer = BearerTokenAuth(simple_validator, prefix="user_")
        token = bearer.generate_token()
        assert token.startswith("user_")

    def test_generate_token_uniqueness(self):
        bearer = BearerTokenAuth(simple_validator)
        assert bearer.generate_token() != bearer.generate_token()

    def test_generate_token_is_valid_credential(self):
        """A generated token (with prefix) is accepted by the same auth source."""
        stored: list[str] = []

        async def storing_validator(credential: str) -> dict:
            stored.append(credential)
            return {"token": credential}

        bearer = BearerTokenAuth(storing_validator, prefix="user_")
        token = bearer.generate_token()

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(bearer)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert stored == [token]


class TestCookieAuth:
    def test_valid_cookie_returns_identity(self):
        cookie_auth = CookieAuth("session", cookie_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(cookie_auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", cookies={"session": VALID_COOKIE})
        assert response.status_code == 200
        assert response.json() == {"session": VALID_COOKIE}

    def test_missing_cookie_returns_401(self):
        cookie_auth = CookieAuth("session", cookie_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(cookie_auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me")
        assert response.status_code == 401

    def test_invalid_cookie_returns_401(self):
        cookie_auth = CookieAuth("session", cookie_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(cookie_auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", cookies={"session": "wrong"})
        assert response.status_code == 401

    def test_kwargs_forwarded_to_validator(self):
        async def session_validator(value: str, *, scope: str) -> dict:
            if value != VALID_COOKIE:
                raise UnauthorizedError()
            return {"session": value, "scope": scope}

        cookie_auth = CookieAuth("session", session_validator, scope="read")

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(cookie_auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", cookies={"session": VALID_COOKIE})
        assert response.status_code == 200
        assert response.json() == {"session": VALID_COOKIE, "scope": "read"}

    @pytest.mark.anyio
    async def test_extract_no_cookie(self):
        from starlette.requests import Request

        auth = CookieAuth("session", cookie_validator)
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        request = Request(scope)
        assert await auth.extract(request) is None

    @pytest.mark.anyio
    async def test_extract_cookie_present(self):
        from starlette.requests import Request

        auth = CookieAuth("session", cookie_validator)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", b"session=abc")],
        }
        request = Request(scope)
        assert await auth.extract(request) == "abc"


class TestAPIKeyHeaderAuth:
    def test_valid_key_returns_identity(self):
        auth = APIKeyHeaderAuth("X-API-Key", simple_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"X-API-Key": VALID_TOKEN})
        assert response.status_code == 200
        assert response.json() == {"user": "alice"}

    def test_missing_header_returns_401(self):
        auth = APIKeyHeaderAuth("X-API-Key", simple_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me")
        assert response.status_code == 401

    def test_invalid_key_returns_401(self):
        auth = APIKeyHeaderAuth("X-API-Key", simple_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"X-API-Key": "wrong"})
        assert response.status_code == 401

    def test_kwargs_forwarded_to_validator(self):
        auth = APIKeyHeaderAuth("X-API-Key", role_validator, role="admin")

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"X-API-Key": VALID_TOKEN})
        assert response.status_code == 200
        assert response.json() == {"user": "alice", "role": "admin"}

    def test_require_forwards_kwargs(self):
        auth = APIKeyHeaderAuth("X-API-Key", role_validator)

        def setup(app: FastAPI):
            @app.get("/admin")
            async def admin(user=Security(auth.require(role="admin"))):
                return user

        client = TestClient(_app(setup))
        response = client.get("/admin", headers={"X-API-Key": VALID_TOKEN})
        assert response.status_code == 200
        assert response.json() == {"user": "alice", "role": "admin"}

    def test_require_preserves_name(self):
        auth = APIKeyHeaderAuth("X-API-Key", simple_validator)
        derived = auth.require(role="admin")
        assert derived._name == "X-API-Key"

    def test_require_does_not_mutate_original(self):
        auth = APIKeyHeaderAuth("X-API-Key", role_validator, role="user")
        auth.require(role="admin")
        assert auth._kwargs == {"role": "user"}

    def test_in_multi_auth(self):
        """APIKeyHeaderAuth.authenticate() is exercised inside MultiAuth."""
        bearer = BearerTokenAuth(simple_validator)
        api_key = APIKeyHeaderAuth("X-API-Key", simple_validator)
        multi = MultiAuth(bearer, api_key)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(multi)):
                return user

        client = TestClient(_app(setup))
        # No bearer → falls through to API key header
        response = client.get("/me", headers={"X-API-Key": VALID_TOKEN})
        assert response.status_code == 200
        assert response.json() == {"user": "alice"}

    def test_is_auth_source(self):
        auth = APIKeyHeaderAuth("X-API-Key", simple_validator)
        assert isinstance(auth, AuthSource)

    @pytest.mark.anyio
    async def test_extract_no_header(self):
        from starlette.requests import Request

        auth = APIKeyHeaderAuth("X-API-Key", simple_validator)
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        request = Request(scope)
        assert await auth.extract(request) is None

    @pytest.mark.anyio
    async def test_extract_empty_header(self):
        from starlette.requests import Request

        auth = APIKeyHeaderAuth("X-API-Key", simple_validator)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-api-key", b"")],
        }
        request = Request(scope)
        assert await auth.extract(request) is None

    @pytest.mark.anyio
    async def test_extract_key_present(self):
        from starlette.requests import Request

        auth = APIKeyHeaderAuth("X-API-Key", simple_validator)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-api-key", b"mykey")],
        }
        request = Request(scope)
        assert await auth.extract(request) == "mykey"


class TestMultiAuth:
    def test_first_source_matches(self):
        bearer = BearerTokenAuth(simple_validator)
        cookie = CookieAuth("session", cookie_validator)
        multi = MultiAuth(bearer, cookie)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(multi)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert response.status_code == 200
        assert response.json() == {"user": "alice"}

    def test_second_source_matches_when_first_absent(self):
        bearer = BearerTokenAuth(simple_validator)
        cookie = CookieAuth("session", cookie_validator)
        multi = MultiAuth(bearer, cookie)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(multi)):
                return user

        client = TestClient(_app(setup))
        # No Authorization header — falls through to cookie
        response = client.get("/me", cookies={"session": VALID_COOKIE})
        assert response.status_code == 200
        assert response.json() == {"session": VALID_COOKIE}

    def test_no_source_matches_returns_401(self):
        bearer = BearerTokenAuth(simple_validator)
        cookie = CookieAuth("session", cookie_validator)
        multi = MultiAuth(bearer, cookie)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(multi)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me")
        assert response.status_code == 401

    def test_invalid_credential_does_not_fallthrough(self):
        """If a credential is found but invalid, the next source is NOT tried."""
        second_called: list[bool] = []

        async def tracking_validator(credential: str) -> dict:
            second_called.append(True)
            return {"from": "second"}

        bearer = BearerTokenAuth(simple_validator)  # raises on wrong token
        cookie = CookieAuth("session", tracking_validator)
        multi = MultiAuth(bearer, cookie)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(multi)):
                return user

        client = TestClient(_app(setup))
        # Bearer credential present but wrong — should NOT try cookie
        response = client.get(
            "/me",
            headers={"Authorization": "Bearer wrong"},
            cookies={"session": VALID_COOKIE},
        )
        assert response.status_code == 401
        assert second_called == []  # cookie validator was never called

    def test_prefix_routes_to_correct_source(self):
        """Prefix-based dispatch: only the matching source's validator is called."""
        user_calls: list[str] = []
        org_calls: list[str] = []

        async def user_validator(credential: str) -> dict:
            user_calls.append(credential)
            return {"type": "user", "id": credential}

        async def org_validator(credential: str) -> dict:
            org_calls.append(credential)
            return {"type": "org", "id": credential}

        user_bearer = BearerTokenAuth(user_validator, prefix="user_")
        org_bearer = BearerTokenAuth(org_validator, prefix="org_")
        multi = MultiAuth(user_bearer, org_bearer)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(multi)):
                return user

        client = TestClient(_app(setup))

        response = client.get("/me", headers={"Authorization": "Bearer user_alice"})
        assert response.status_code == 200
        assert response.json() == {"type": "user", "id": "user_alice"}
        assert user_calls == ["user_alice"]
        assert org_calls == []

        user_calls.clear()

        response = client.get("/me", headers={"Authorization": "Bearer org_acme"})
        assert response.status_code == 200
        assert response.json() == {"type": "org", "id": "org_acme"}
        assert user_calls == []
        assert org_calls == ["org_acme"]

    def test_require_returns_new_multi_auth(self):
        from fastapi_toolsets.security.sources import MultiAuth as MultiAuthClass

        bearer = BearerTokenAuth(role_validator)
        multi = MultiAuth(bearer)
        derived = multi.require(role="admin")
        assert isinstance(derived, MultiAuthClass)
        assert derived is not multi

    def test_require_forwards_kwargs_to_sources(self):
        """multi.require() propagates to all sources that support it."""
        bearer = BearerTokenAuth(role_validator)
        multi = MultiAuth(bearer)

        def setup(app: FastAPI):
            @app.get("/admin")
            async def admin(user=Security(multi.require(role="admin"))):
                return user

        client = TestClient(_app(setup))
        response = client.get(
            "/admin", headers={"Authorization": f"Bearer {VALID_TOKEN}"}
        )
        assert response.status_code == 200
        assert response.json() == {"user": "alice", "role": "admin"}

    def test_require_skips_sources_without_require(self):
        """Sources without require() are passed through unchanged."""
        header_auth = _HeaderAuth(secret="s3cr3t")
        multi = MultiAuth(header_auth)
        derived = multi.require(role="admin")
        assert derived._sources[0] is header_auth

    def test_require_does_not_mutate_original(self):
        bearer = BearerTokenAuth(role_validator, role="user")
        multi = MultiAuth(bearer)
        multi.require(role="admin")
        assert bearer._kwargs == {"role": "user"}

    def test_require_mixed_sources(self):
        """require() applies to sources with require(), skips those without."""
        from typing import cast

        bearer = BearerTokenAuth(role_validator)
        header_auth = _HeaderAuth(secret="s3cr3t")
        multi = MultiAuth(bearer, header_auth)
        derived = multi.require(role="admin")
        # bearer got require() applied, header_auth passed through
        assert cast(BearerTokenAuth, derived._sources[0])._kwargs == {"role": "admin"}
        assert derived._sources[1] is header_auth


class TestRequire:
    def test_bearer_require_forwards_kwargs(self):
        """require() creates a new instance that passes merged kwargs to validator."""
        bearer = BearerTokenAuth(role_validator)

        def setup(app: FastAPI):
            @app.get("/admin")
            async def admin(user=Security(bearer.require(role="admin"))):
                return user

        client = TestClient(_app(setup))
        response = client.get(
            "/admin", headers={"Authorization": f"Bearer {VALID_TOKEN}"}
        )
        assert response.status_code == 200
        assert response.json() == {"user": "alice", "role": "admin"}

    def test_bearer_require_overrides_existing_kwarg(self):
        """require() kwargs override kwargs set at instantiation."""
        bearer = BearerTokenAuth(role_validator, role="user")

        def setup(app: FastAPI):
            @app.get("/admin")
            async def admin(user=Security(bearer.require(role="admin"))):
                return user

        client = TestClient(_app(setup))
        response = client.get(
            "/admin", headers={"Authorization": f"Bearer {VALID_TOKEN}"}
        )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"

    def test_bearer_require_preserves_prefix(self):
        """require() keeps the prefix of the original instance."""
        bearer = BearerTokenAuth(role_validator, prefix="user_")
        derived = bearer.require(role="admin")
        assert derived._prefix == "user_"

    def test_bearer_require_does_not_mutate_original(self):
        """require() returns a new instance — original kwargs are unchanged."""
        bearer = BearerTokenAuth(role_validator, role="user")
        bearer.require(role="admin")
        assert bearer._kwargs == {"role": "user"}

    def test_cookie_require_forwards_kwargs(self):
        async def scoped_validator(value: str, *, scope: str) -> dict:
            if value != VALID_COOKIE:
                raise UnauthorizedError()
            return {"session": value, "scope": scope}

        cookie = CookieAuth("session", scoped_validator)

        def setup(app: FastAPI):
            @app.get("/admin")
            async def admin(user=Security(cookie.require(scope="admin"))):
                return user

        client = TestClient(_app(setup))
        response = client.get("/admin", cookies={"session": VALID_COOKIE})
        assert response.status_code == 200
        assert response.json() == {"session": VALID_COOKIE, "scope": "admin"}

    def test_cookie_require_preserves_name(self):
        cookie = CookieAuth("session", cookie_validator)
        derived = cookie.require(scope="admin")
        assert derived._name == "session"

    def test_bearer_require_in_multi_auth(self):
        """require() instances work seamlessly inside MultiAuth."""
        PREFIXED_TOKEN = f"user_{VALID_TOKEN}"

        async def prefixed_role_validator(credential: str, *, role: str) -> dict:
            if credential != PREFIXED_TOKEN:
                raise UnauthorizedError()
            return {"user": "alice", "role": role}

        bearer = BearerTokenAuth(prefixed_role_validator, prefix="user_")
        multi = MultiAuth(bearer.require(role="admin"))

        def setup(app: FastAPI):
            @app.get("/admin")
            async def admin(user=Security(multi)):
                return user

        client = TestClient(_app(setup))
        response = client.get(
            "/admin", headers={"Authorization": f"Bearer {PREFIXED_TOKEN}"}
        )
        assert response.status_code == 200
        assert response.json() == {"user": "alice", "role": "admin"}


class TestSyncValidators:
    """Sync (non-async) validators — covers the sync path in _call_validator."""

    def test_bearer_sync_validator(self):
        def sync_validator(credential: str) -> dict:
            if credential != VALID_TOKEN:
                raise UnauthorizedError()
            return {"user": "alice"}

        bearer = BearerTokenAuth(sync_validator)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(bearer)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert response.status_code == 200
        assert response.json() == {"user": "alice"}

    def test_sync_validator_via_authenticate(self):
        """authenticate() with sync validator (MultiAuth path)."""

        def sync_validator(credential: str) -> dict:
            if credential != VALID_TOKEN:
                raise UnauthorizedError()
            return {"user": "alice"}

        bearer = BearerTokenAuth(sync_validator)
        multi = MultiAuth(bearer)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(multi)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert response.status_code == 200
        assert response.json() == {"user": "alice"}


class TestCookieAuthSigned:
    """CookieAuth with HMAC-SHA256 signed cookies (secret_key path)."""

    SECRET = "test-hmac-secret"

    def test_valid_signed_cookie_via_set_cookie(self):
        """set_cookie signs the value; the signed cookie is verified on read."""
        from fastapi import Response

        auth = CookieAuth("session", cookie_validator, secret_key=self.SECRET)

        def setup(app: FastAPI):
            @app.get("/login")
            async def login(response: Response):
                auth.set_cookie(response, VALID_COOKIE)
                return {"ok": True}

            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        with TestClient(_app(setup)) as client:
            client.get("/login")
            response = client.get("/me")
        assert response.status_code == 200
        assert response.json() == {"session": VALID_COOKIE}

    def test_tampered_signature_returns_401(self):
        """A cookie whose HMAC signature has been modified is rejected."""
        import base64 as _b64
        import json as _json
        import time as _time

        auth = CookieAuth("session", cookie_validator, secret_key=self.SECRET)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        data = _b64.urlsafe_b64encode(
            _json.dumps({"v": VALID_COOKIE, "exp": int(_time.time()) + 9999}).encode()
        ).decode()
        response = client.get("/me", cookies={"session": f"{data}.invalidsig"})
        assert response.status_code == 401

    def test_expired_signed_cookie_returns_401(self):
        """A signed cookie past its expiry timestamp is rejected."""
        import base64 as _b64
        import hashlib as _hashlib
        import hmac as _hmac
        import json as _json
        import time as _time

        auth = CookieAuth("session", cookie_validator, secret_key=self.SECRET)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        data = _b64.urlsafe_b64encode(
            _json.dumps({"v": VALID_COOKIE, "exp": int(_time.time()) - 1}).encode()
        ).decode()
        sig = _hmac.new(
            self.SECRET.encode(), data.encode(), _hashlib.sha256
        ).hexdigest()
        response = client.get("/me", cookies={"session": f"{data}.{sig}"})
        assert response.status_code == 401

    def test_invalid_json_payload_returns_401(self):
        """A signed cookie whose payload is not valid JSON is rejected."""
        import base64 as _b64
        import hashlib as _hashlib
        import hmac as _hmac

        auth = CookieAuth("session", cookie_validator, secret_key=self.SECRET)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        data = _b64.urlsafe_b64encode(b"not-valid-json").decode()
        sig = _hmac.new(
            self.SECRET.encode(), data.encode(), _hashlib.sha256
        ).hexdigest()
        response = client.get("/me", cookies={"session": f"{data}.{sig}"})
        assert response.status_code == 401

    def test_malformed_cookie_no_dot_returns_401(self):
        """A signed cookie without the dot separator is rejected."""
        auth = CookieAuth("session", cookie_validator, secret_key=self.SECRET)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", cookies={"session": "nodothere"})
        assert response.status_code == 401

    def test_set_cookie_without_secret(self):
        """set_cookie without secret_key writes the raw value."""
        from starlette.responses import Response as StarletteResponse

        auth = CookieAuth("session", cookie_validator)
        response = StarletteResponse()
        auth.set_cookie(response, "rawvalue")
        assert "session=rawvalue" in response.headers["set-cookie"]

    def test_delete_cookie(self):
        """delete_cookie produces a Set-Cookie header that clears the session."""
        from starlette.responses import Response as StarletteResponse

        auth = CookieAuth("session", cookie_validator)
        response = StarletteResponse()
        auth.delete_cookie(response)
        assert "session" in response.headers["set-cookie"]


# Minimal concrete subclass used only in tests below.
class _HeaderAuth(AuthSource):
    """Reads a custom X-Token header — no FastAPI security scheme."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secret = secret

    async def extract(self, request) -> str | None:
        return request.headers.get("X-Token") or None

    async def authenticate(self, credential: str) -> dict:
        if credential != self._secret:
            raise UnauthorizedError()
        return {"token": credential}


class TestAuthSource:
    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            AuthSource()

    def test_builtin_classes_are_auth_sources(self):
        bearer = BearerTokenAuth(simple_validator)
        cookie = CookieAuth("session", cookie_validator)
        api_key = APIKeyHeaderAuth("X-API-Key", simple_validator)
        assert isinstance(bearer, AuthSource)
        assert isinstance(cookie, AuthSource)
        assert isinstance(api_key, AuthSource)

    def test_custom_source_standalone_valid(self):
        """Default __call__ wires extract + authenticate via Request injection."""
        auth = _HeaderAuth(secret="s3cr3t")

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"X-Token": "s3cr3t"})
        assert response.status_code == 200
        assert response.json() == {"token": "s3cr3t"}

    def test_custom_source_standalone_missing_credential(self):
        auth = _HeaderAuth(secret="s3cr3t")

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me")  # no X-Token header
        assert response.status_code == 401

    def test_custom_source_standalone_invalid_credential(self):
        auth = _HeaderAuth(secret="s3cr3t")

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(auth)):
                return user

        client = TestClient(_app(setup))
        response = client.get("/me", headers={"X-Token": "wrong"})
        assert response.status_code == 401

    def test_custom_source_in_multi_auth(self):
        """Custom AuthSource works transparently inside MultiAuth."""
        header_auth = _HeaderAuth(secret="s3cr3t")
        bearer = BearerTokenAuth(simple_validator)
        multi = MultiAuth(bearer, header_auth)

        def setup(app: FastAPI):
            @app.get("/me")
            async def me(user=Security(multi)):
                return user

        client = TestClient(_app(setup))

        # Bearer matches first
        response = client.get("/me", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert response.status_code == 200
        assert response.json() == {"user": "alice"}

        # No bearer → falls through to custom header source
        response = client.get("/me", headers={"X-Token": "s3cr3t"})
        assert response.status_code == 200
        assert response.json() == {"token": "s3cr3t"}
