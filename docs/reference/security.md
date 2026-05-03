# `security`

Here's the reference for the authentication helpers provided by the `security` module.

You can import them directly from `fastapi_toolsets.security`:

```python
from fastapi_toolsets.security import (
    AuthSource,
    BearerTokenAuth,
    CookieAuth,
    APIKeyHeaderAuth,
    MultiAuth,
    oauth_build_authorization_redirect,
    oauth_decode_state,
    oauth_encode_state,
    oauth_fetch_userinfo,
    oauth_resolve_provider_urls,
)
```

## ::: fastapi_toolsets.security.AuthSource

## ::: fastapi_toolsets.security.BearerTokenAuth

## ::: fastapi_toolsets.security.CookieAuth

## ::: fastapi_toolsets.security.APIKeyHeaderAuth

## ::: fastapi_toolsets.security.MultiAuth

## ::: fastapi_toolsets.security.oauth_resolve_provider_urls

## ::: fastapi_toolsets.security.oauth_fetch_userinfo

## ::: fastapi_toolsets.security.oauth_build_authorization_redirect

## ::: fastapi_toolsets.security.oauth_encode_state

## ::: fastapi_toolsets.security.oauth_decode_state
