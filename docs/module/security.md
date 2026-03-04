# Security

Composable authentication helpers for FastAPI that use `Security()` for OpenAPI documentation and accept user-provided validator functions with full type flexibility.

## Overview

The `security` module provides four auth source classes and a `MultiAuth` factory. Each class wraps a FastAPI security scheme for OpenAPI and accepts a validator function called as:

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

The optional `prefix` parameter restricts a `BearerTokenAuth` instance to tokens
that start with a given string. The prefix is **kept** in the value passed to the
validator — store and compare tokens with their prefix included.

This lets you deploy multiple `BearerTokenAuth` instances in the same application
and disambiguate them efficiently in `MultiAuth`:

```python
user_bearer = BearerTokenAuth(verify_user, prefix="user_")  # matches "Bearer user_..."
org_bearer  = BearerTokenAuth(verify_org,  prefix="org_")   # matches "Bearer org_..."
```

Use [`generate_token()`](#token-generation) to create correctly-prefixed tokens.

#### Token generation

`BearerTokenAuth.generate_token()` produces a secure random token ready to store
in your database and return to the client. If a prefix is configured it is
prepended automatically:

```python
bearer = BearerTokenAuth(verify_token, prefix="user_")

token = bearer.generate_token()   # e.g. "user_Xk3mN..."
await db.store_token(user_id, token)
return {"access_token": token, "token_type": "bearer"}
```

The client sends `Authorization: Bearer user_Xk3mN...` and the validator receives
the full token (prefix included) to compare against the stored value.

### [`CookieAuth`](../reference/security.md#fastapi_toolsets.security.CookieAuth)

Reads a named cookie. Wraps `APIKeyCookie` for OpenAPI.

```python
from fastapi_toolsets.security import CookieAuth

cookie_auth = CookieAuth("session", validator=verify_session)

@app.get("/me")
async def me(user: User = Security(cookie_auth)):
    return user
```

### [`OAuth2Auth`](../reference/security.md#fastapi_toolsets.security.OAuth2Auth)

Reads the `Authorization: Bearer <token>` header and registers the token endpoint
in OpenAPI via `OAuth2PasswordBearer`.

```python
from fastapi_toolsets.security import OAuth2Auth

oauth2_auth = OAuth2Auth(token_url="/token", validator=verify_token)

@app.get("/me")
async def me(user: User = Security(oauth2_auth)):
    return user
```

### [`OpenIDAuth`](../reference/security.md#fastapi_toolsets.security.OpenIDAuth)

Reads the `Authorization: Bearer <token>` header and registers the OpenID Connect
discovery URL in OpenAPI via `OpenIdConnect`. Token validation is fully delegated
to your validator — use any OIDC / JWT library (`authlib`, `python-jose`, `PyJWT`).

```python
from fastapi_toolsets.security import OpenIDAuth

async def verify_google_token(token: str, *, audience: str) -> User:
    payload = jwt.decode(token, google_public_keys, algorithms=["RS256"],
                         audience=audience)
    return User(email=payload["email"], name=payload["name"])

google_auth = OpenIDAuth(
    "https://accounts.google.com/.well-known/openid-configuration",
    verify_google_token,
    audience="my-client-id",
)

@app.get("/me")
async def me(user: User = Security(google_auth)):
    return user
```

The discovery URL is used **only for OpenAPI documentation** — no requests are made
to it by this class. You are responsible for fetching and caching the provider's
public keys in your validator.

Multiple providers work naturally with `MultiAuth`:

```python
multi = MultiAuth(google_auth, github_auth)

@app.get("/data")
async def data(user: User = Security(multi)):
    return user
```

## Typed validator kwargs

All auth classes forward extra instantiation keyword arguments to the validator.
Arguments can be any type — enums, strings, integers, etc. The validator returns
the authenticated identity, which FastAPI injects directly into the route handler.

```python
async def verify_token(token: str, *, role: Role, permission: str) -> User:
    user = await decode_token(token)
    if user.role != role or permission not in user.permissions:
        raise UnauthorizedError()
    return user

bearer = BearerTokenAuth(verify_token, role=Role.ADMIN, permission="billing:read")
```

Each auth instance is self-contained — create a separate instance per distinct
requirement instead of passing requirements through `Security(scopes=[...])`.

### Using `.require()` inline

If declaring a new top-level variable per role feels verbose, use `.require()` to
create a configured clone directly in the route decorator. The original instance
is not mutated:

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
The `prefix` (for `BearerTokenAuth`) and cookie name (for `CookieAuth`) are
always preserved.

`.require()` instances work transparently inside `MultiAuth`:

```python
multi = MultiAuth(
    user_bearer.require(role=Role.USER),
    org_bearer.require(role=Role.ADMIN),
)
```

## MultiAuth

[`MultiAuth`](../reference/security.md#fastapi_toolsets.security.MultiAuth) combines
multiple auth sources into a single callable. Sources are tried in order; the
first one that finds a credential wins.

```python
from fastapi_toolsets.security import MultiAuth

multi = MultiAuth(user_bearer, org_bearer, cookie_auth)

@app.get("/data")
async def data_route(user = Security(multi)):
    return user
```

### Using `.require()` on MultiAuth

`MultiAuth` also supports `.require()`, which propagates the kwargs to every
source that implements it. Sources that do not (e.g. custom `AuthSource`
subclasses) are passed through unchanged:

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

Because `extract()` is pure string matching (no I/O), prefix-based source
selection is essentially free. Only the matching source's validator (which may
involve DB or network I/O) is ever called:

```python
user_bearer = BearerTokenAuth(verify_user, prefix="user_")
org_bearer  = BearerTokenAuth(verify_org,  prefix="org_")

multi = MultiAuth(user_bearer, org_bearer)

# "Bearer user_alice" → only verify_user runs, receives "user_alice"
# "Bearer org_acme"   → only verify_org runs, receives "org_acme"
```

Tokens are stored and compared **with their prefix** — use `generate_token()` on
each source to issue correctly-prefixed tokens:

```python
user_token = user_bearer.generate_token()  # "user_..."
org_token  = org_bearer.generate_token()   # "org_..."
```

---

[:material-api: API Reference](../reference/security.md)
