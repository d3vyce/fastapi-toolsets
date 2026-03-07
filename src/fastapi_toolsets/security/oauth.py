"""OAuth 2.0 / OIDC helper utilities."""

import base64
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi.responses import RedirectResponse

_discovery_cache: dict[str, dict] = {}


async def oauth_resolve_provider_urls(
    discovery_url: str,
) -> tuple[str, str, str | None]:
    """Fetch the OIDC discovery document and return endpoint URLs.

    Args:
        discovery_url: URL of the provider's ``/.well-known/openid-configuration``.

    Returns:
        A ``(authorization_url, token_url, userinfo_url)`` tuple.
        *userinfo_url* is ``None`` when the provider does not advertise one.
    """
    if discovery_url not in _discovery_cache:
        async with httpx.AsyncClient() as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            _discovery_cache[discovery_url] = resp.json()
    cfg = _discovery_cache[discovery_url]
    return (
        cfg["authorization_endpoint"],
        cfg["token_endpoint"],
        cfg.get("userinfo_endpoint"),
    )


async def oauth_fetch_userinfo(
    *,
    token_url: str,
    userinfo_url: str,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Exchange an authorization code for tokens and return the userinfo payload.

    Performs the two-step OAuth 2.0 / OIDC token exchange:

    1. POSTs the authorization *code* to *token_url* to obtain an access token.
    2. GETs *userinfo_url* using that access token as a Bearer credential.

    Args:
        token_url: Provider's token endpoint.
        userinfo_url: Provider's userinfo endpoint.
        code: Authorization code received from the provider's callback.
        client_id: OAuth application client ID.
        client_secret: OAuth application client secret.
        redirect_uri: Redirect URI that was used in the authorization request.

    Returns:
        The JSON payload returned by the userinfo endpoint as a plain ``dict``.
    """
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        userinfo_resp = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo_resp.raise_for_status()
        return userinfo_resp.json()


def oauth_build_authorization_redirect(
    authorization_url: str,
    *,
    client_id: str,
    scopes: str,
    redirect_uri: str,
    destination: str,
) -> RedirectResponse:
    """Return an OAuth 2.0 authorization ``RedirectResponse``.

    Args:
        authorization_url: Provider's authorization endpoint.
        client_id: OAuth application client ID.
        scopes: Space-separated list of requested scopes.
        redirect_uri: URI the provider should redirect back to after authorization.
        destination: URL the user should be sent to after the full OAuth flow
            completes (encoded as ``state``).

    Returns:
        A :class:`~fastapi.responses.RedirectResponse` to the provider's
        authorization page.
    """
    params = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": oauth_encode_state(destination),
        }
    )
    return RedirectResponse(f"{authorization_url}?{params}")


def oauth_encode_state(url: str) -> str:
    """Base64url-encode a URL to embed as an OAuth ``state`` parameter."""
    return base64.urlsafe_b64encode(url.encode()).decode()


def oauth_decode_state(state: str | None, *, fallback: str) -> str:
    """Decode a base64url OAuth ``state`` parameter.

    Handles missing padding (some providers strip ``=``).
    Returns *fallback* if *state* is absent, the literal string ``"null"``,
    or cannot be decoded.
    """
    if not state or state == "null":
        return fallback
    try:
        padded = state + "=" * (4 - len(state) % 4)
        return base64.urlsafe_b64decode(padded).decode()
    except Exception:
        return fallback
