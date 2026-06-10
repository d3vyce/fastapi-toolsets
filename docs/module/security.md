# Security

Composable authentication helpers for FastAPI that use `Security()` for OpenAPI documentation and accept user-provided validator functions with full type flexibility.

## Overview

The `security` module provides four auth source classes, a `MultiAuth` factory, and a set of OAuth 2.0 / OIDC helper utilities. Each auth class wraps a FastAPI security scheme for OpenAPI and accepts a validator function called as:

```python
await validator(credential, **kwargs)
```

where `kwargs` are the extra keyword arguments provided at instantiation (roles, permissions, enums, etc.). The validator returns the authenticated identity (e.g. a `User` model) which becomes the route dependency value.

```python
from fastapi import Security
from fastapi_toolsets.security import BearerTokenAuth

async def verify_token(token: str, *, role: str) -> User:
    user = await db.get_by_token(token)
    if not user or user.role != role:
        raise UnauthorizedError()
    return user

bearer_admin = BearerTokenAuth(verify_token, role="admin")

@app.get("/admin")
async def admin_route(user: User = Security(bearer_admin)):
    return user
```

## Auth sources

### [`BearerTokenAuth`](../reference/security.md#fastapi_toolsets.security.BearerTokenAuth)

Reads the `Authorization: Bearer <token>` header. Wraps `HTTPBearer` for OpenAPI.

```python
from fastapi_toolsets.security import BearerTokenAuth

bearer = BearerTokenAuth(validator=verify_token)

@app.get("/me")
async def me(user: User = Security(bearer)):
    return user
```

#### Token prefix

The optional `prefix` parameter restricts a `BearerTokenAuth` instance to tokens that start with a given string. The prefix is **kept** in the value passed to the validator — store and compare tokens with their prefix included.

This lets you deploy multiple `BearerTokenAuth` instances in the same application and disambiguate them efficiently in `MultiAuth`:

```python
user_bearer = BearerTokenAuth(verify_user, prefix="user_")  # matches "Bearer user_..."
org_bearer  = BearerTokenAuth(verify_org,  prefix="org_")   # matches "Bearer org_..."
```

Use [`generate_token()`](#token-generation) to create correctly-prefixed tokens.

#### Token generation

`BearerTokenAuth.generate_token()` produces a secure random token ready to store in your database and return to the client. If a prefix is configured it is prepended automatically:

```python
bearer = BearerTokenAuth(verify_token, prefix="user_")

token = bearer.generate_token()   # e.g. "user_Xk3mN..."
await db.store_token(user_id, token)
return {"access_token": token, "token_type": "bearer"}
```

The client sends `Authorization: Bearer user_Xk3mN...` and the validator receives the full token (prefix included) to compare against the stored value.

### [`CookieAuth`](../reference/security.md#fastapi_toolsets.security.CookieAuth)

Reads a named cookie. Wraps `APIKeyCookie` for OpenAPI.

Cookies are issued with the `Secure` flag set by default, meaning they are only transmitted over HTTPS. Set `secure=False` when running locally over plain HTTP:

```python
from fastapi_toolsets.security import CookieAuth

# Production (HTTPS) — default
cookie_auth = CookieAuth("session", validator=verify_session)

# Local development (HTTP only)
cookie_auth = CookieAuth("session", validator=verify_session, secure=False)

@app.get("/me")
async def me(user: User = Security(cookie_auth)):
    return user
```

#### Signed cookies

Pass `secret_key` to enable HMAC-SHA256 signed, tamper-proof cookies. The cookie payload includes an expiry timestamp (`ttl`, default 24 h). No database entry is required — the signature is self-contained.

Use `set_cookie()` to issue the signed cookie on login and `delete_cookie()` to clear it on logout:

```python
# Production
cookie_auth = CookieAuth("session", verify_session, secret_key="your-secret")

# Local development
cookie_auth = CookieAuth("session", verify_session, secret_key="your-secret", secure=False)

@app.post("/login")
async def login(response: Response):
    cookie_auth.set_cookie(response, user_id)
    return {"ok": True}

@app.post("/logout")
async def logout(response: Response):
    cookie_auth.delete_cookie(response)
    return {"ok": True}

@app.get("/me")
async def me(user: User = Security(cookie_auth)):
    return user
```

When `secret_key` is not set, the raw cookie value is passed directly to the validator (stateful session behaviour — you manage the session store).

### [`APIKeyHeaderAuth`](../reference/security.md#fastapi_toolsets.security.APIKeyHeaderAuth)

Reads an API key from a named HTTP header. Wraps `APIKeyHeader` for OpenAPI.

```python
from fastapi_toolsets.security import APIKeyHeaderAuth

api_key_auth = APIKeyHeaderAuth("X-API-Key", validator=verify_api_key)

@app.get("/data")
async def data(user: User = Security(api_key_auth)):
    return user
```

The header name is configurable — use any header your API defines (e.g. `"X-API-Key"`, `"Authorization"`, `"X-Service-Token"`).

## Typed validator kwargs

All auth classes forward extra instantiation keyword arguments to the validator. Arguments can be any type — enums, strings, integers, etc. The validator returns the authenticated identity, which FastAPI injects directly into the route handler.

```python
async def verify_token(token: str, *, role: Role, permission: str) -> User:
    user = await decode_token(token)
    if user.role != role or permission not in user.permissions:
        raise UnauthorizedError()
    return user

bearer = BearerTokenAuth(verify_token, role=Role.ADMIN, permission="billing:read")
```

Each auth instance is self-contained — create a separate instance per distinct requirement instead of passing requirements through `Security(scopes=[...])`.

### Security scopes

Scopes declared on a route via `Security(auth, scopes=[...])` are never silently ignored. If the validator declares a `scopes` parameter, it receives the declared scopes (`list[str]`, empty when none are declared) and is responsible for enforcing them:

```python
async def verify_token(token: str, scopes: list[str]) -> User:
    user = await decode_token(token)
    if not set(scopes) <= user.scopes:
        raise UnauthorizedError()
    return user
```

If the validator does **not** declare a `scopes` parameter, declaring scopes on a route raises `RuntimeError` at request time — the route advertises an authorization check in OpenAPI that the auth source cannot enforce. Custom `AuthSource` subclasses get the same fail-closed behaviour by default; override `authenticate_scoped(credential, scopes)` to support scopes.

Because `scopes` is injected automatically, it is reserved: passing it as a validator kwarg at instantiation (or through `.require()`) raises `ValueError` at startup. Use a different keyword name for instance-bound requirements.

### Using `.require()` inline

If declaring a new top-level variable per role feels verbose, use `.require()` to create a configured clone directly in the route decorator. The original instance is not mutated:

```python
bearer = BearerTokenAuth(verify_token)

@app.get("/admin/stats")
async def admin_stats(user: User = Security(bearer.require(role=Role.ADMIN))):
    return {"message": f"Hello admin {user.name}"}

@app.get("/profile")
async def profile(user: User = Security(bearer.require(role=Role.USER))):
    return {"id": user.id, "name": user.name}
```

`.require()` kwargs are merged over existing ones — new values win on conflict.
The `prefix` (for `BearerTokenAuth`), cookie name and `secret_key` (for
`CookieAuth`), and header name (for `APIKeyHeaderAuth`) are always preserved.

## MultiAuth

[`MultiAuth`](../reference/security.md#fastapi_toolsets.security.MultiAuth) combines multiple auth sources into a single callable. Sources are tried in order; the first one that finds a credential wins.

If a credential is extracted but the validator raises, the exception propagates immediately — the remaining sources are **not** tried. This prevents silent fallthrough on invalid credentials.

```python
from fastapi_toolsets.security import MultiAuth

multi = MultiAuth(user_bearer, org_bearer, cookie_auth)

@app.get("/data")
async def data_route(user = Security(multi)):
    return user
```

### Using `.require()` on MultiAuth

`MultiAuth` also supports `.require()`, which propagates the kwargs to every source that implements it. Sources that do not (e.g. custom `AuthSource` subclasses) are passed through unchanged:

```python
multi = MultiAuth(bearer, cookie)

@app.get("/admin")
async def admin(user: User = Security(multi.require(role=Role.ADMIN))):
    return user
```

This is equivalent to calling `.require()` on each source individually:

```python
# These two are identical
multi.require(role=Role.ADMIN)

MultiAuth(
    bearer.require(role=Role.ADMIN),
    cookie.require(role=Role.ADMIN),
)
```

### Prefix-based dispatch

Because `extract()` is pure string matching (no I/O), prefix-based source selection is essentially free. Only the matching source's validator (which may involve DB or network I/O) is ever called:

```python
user_bearer = BearerTokenAuth(verify_user, prefix="user_")
org_bearer  = BearerTokenAuth(verify_org,  prefix="org_")

multi = MultiAuth(user_bearer, org_bearer)

# "Bearer user_alice" → only verify_user runs, receives "user_alice"
# "Bearer org_acme"   → only verify_org runs, receives "org_acme"
```

Tokens are stored and compared **with their prefix** — use `generate_token()` on each source to issue correctly-prefixed tokens:

```python
user_token = user_bearer.generate_token()  # "user_..."
org_token  = org_bearer.generate_token()   # "org_..."
```

## Custom auth sources

Subclass [`AuthSource`](../reference/security.md#fastapi_toolsets.security.AuthSource) to implement any credential extraction strategy. You only need to implement `extract()` and `authenticate()`:

```python
from fastapi_toolsets.security import AuthSource
from fastapi_toolsets.exceptions import UnauthorizedError

class MTLSAuth(AuthSource):
    async def extract(self, request) -> str | None:
        return request.headers.get("X-Client-Cert-DN") or None

    async def authenticate(self, credential: str):
        dn = parse_dn(credential)
        if dn.get("O") != "MyOrg":
            raise UnauthorizedError()
        return {"dn": credential}
```

Custom sources work transparently inside `MultiAuth`.

## OAuth 2.0 / OIDC helpers

The module provides standalone async utilities for building OAuth 2.0 / OIDC login flows. They handle provider discovery, authorization redirects, token exchange, and state encoding — leaving JWT validation and session management to your application.

### Provider discovery

[`oauth_resolve_provider_urls()`](../reference/security.md#fastapi_toolsets.security.oauth_resolve_provider_urls) fetches the OIDC discovery document and returns the endpoint URLs. Results are cached in-process to avoid repeated network calls:

```python
from fastapi_toolsets.security import oauth_resolve_provider_urls

auth_url, token_url, userinfo_url = await oauth_resolve_provider_urls(
    "https://accounts.google.com/.well-known/openid-configuration"
)
```

Returns a `(authorization_url, token_url, userinfo_url)` tuple. `userinfo_url` is `None` when the provider does not advertise one.

### Authorization redirect

[`oauth_build_authorization_redirect()`](../reference/security.md#fastapi_toolsets.security.oauth_build_authorization_redirect) constructs the redirect to the provider's authorization page.  It requires a `state_token` — a random CSRF token generated by [`oauth_generate_state_token()`](../reference/security.md#fastapi_toolsets.security.oauth_generate_state_token) — that must be stored server-side (e.g. in the session) and verified on the callback to prevent login-CSRF attacks ([RFC 6749 §10.12](https://datatracker.ietf.org/doc/html/rfc6749#section-10.12)):

```python
from fastapi import Request
from fastapi_toolsets.security import oauth_build_authorization_redirect, oauth_generate_state_token

@app.get("/auth/google/login")
async def google_login(request: Request):
    auth_url, _, _ = await oauth_resolve_provider_urls(GOOGLE_DISCOVERY_URL)
    state_token = oauth_generate_state_token()
    request.session["oauth_state"] = state_token  # requires SessionMiddleware
    return oauth_build_authorization_redirect(
        auth_url,
        client_id=GOOGLE_CLIENT_ID,
        scopes="openid email profile",
        redirect_uri="https://myapp.com/auth/google/callback",
        destination="/dashboard",
        state_token=state_token,
    )
```

### Token exchange and userinfo

[`oauth_fetch_userinfo()`](../reference/security.md#fastapi_toolsets.security.oauth_fetch_userinfo) performs the two-step exchange: it POSTs the authorization code to the token endpoint, then GETs the userinfo endpoint with the resulting access token.

On the callback, retrieve the stored token and pass it to [`oauth_decode_state()`](../reference/security.md#fastapi_toolsets.security.oauth_decode_state) to verify the CSRF token before processing the code:

```python
from fastapi import HTTPException, Request
from fastapi_toolsets.security import oauth_decode_state, oauth_fetch_userinfo

@app.get("/auth/google/callback")
async def google_callback(request: Request, code: str, state: str):
    # Pop token first — single-use, regardless of whether verification succeeds
    state_token = request.session.pop("oauth_state", None)
    if state_token is None:
        raise HTTPException(status_code=400, detail="missing OAuth state")
    destination = oauth_decode_state(state, expected_state_token=state_token, fallback="/")
    if not destination.startswith("/"):  # reject absolute URLs to prevent open-redirect
        destination = "/"

    _, token_url, userinfo_url = await oauth_resolve_provider_urls(GOOGLE_DISCOVERY_URL)
    userinfo = await oauth_fetch_userinfo(
        token_url=token_url,
        userinfo_url=userinfo_url,
        code=code,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        redirect_uri="https://myapp.com/auth/google/callback",
        required_scopes="openid email profile",
    )
    user = await db.upsert_user(email=userinfo["email"])
    response = RedirectResponse(destination)
    session_cookie.set_cookie(response, str(user.id))
    return response
```

Pass `required_scopes` to guard against providers silently granting fewer scopes than requested — `oauth_fetch_userinfo` raises `ValueError` if any are missing.

### State encoding

[`oauth_encode_state()`](../reference/security.md#fastapi_toolsets.security.oauth_encode_state) and [`oauth_decode_state()`](../reference/security.md#fastapi_toolsets.security.oauth_decode_state) encode and decode the destination URL together with the CSRF token embedded in the OAuth `state` parameter.  `oauth_decode_state` returns `fallback` if `state` is absent, malformed, or the token does not match:

```python
from fastapi_toolsets.security import oauth_encode_state, oauth_decode_state

state_token = oauth_generate_state_token()
encoded = oauth_encode_state("/dashboard", state_token)
decoded = oauth_decode_state(encoded, expected_state_token=state_token, fallback="/")  # "/dashboard"
decoded = oauth_decode_state(encoded, expected_state_token="wrong", fallback="/")  # "/"
decoded = oauth_decode_state(None, expected_state_token=state_token, fallback="/")       # "/"
```

---

[:material-api: API Reference](../reference/security.md)
