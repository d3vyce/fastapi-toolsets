"""OAuth 2.0 / OIDC helper utilities."""

import base64
import binascii
import hmac
import json
import secrets
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from async_lru import alru_cache
from fastapi.responses import RedirectResponse

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"


def _validate_issuer(discovery_url: str, issuer: Any) -> None:
    """Check the discovery document claims the issuer it was fetched from."""
    if not discovery_url.endswith(_DISCOVERY_SUFFIX):
        return
    expected = discovery_url.removesuffix(_DISCOVERY_SUFFIX).rstrip("/")
    if not isinstance(issuer, str) or issuer.rstrip("/") != expected:
        raise ValueError(
            f"discovery document issuer {issuer!r} does not match the "
            f"expected issuer {expected!r} derived from the discovery URL"
        )


def _require_https(url: str, description: str) -> str:
    """Reject OAuth URLs that would send credentials over plaintext HTTP."""
    parsed = urlsplit(url)
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS:
        return url
    raise ValueError(f"{description} must use https:// (got {url!r})")


@alru_cache(maxsize=32)
async def oauth_resolve_provider_urls(
    discovery_url: str,
) -> tuple[str, str, str | None]:
    """Fetch the OIDC discovery document and return endpoint URLs.

    Args:
        discovery_url: URL of the provider's ``/.well-known/openid-configuration``.

    Returns:
        A ``(authorization_url, token_url, userinfo_url)`` tuple.
        *userinfo_url* is ``None`` when the provider does not advertise one.

    Raises:
        ValueError: If *discovery_url* or any endpoint in the discovery
            document is not HTTPS (loopback hosts excepted), or if the
            document's ``issuer`` does not match the discovery URL.
    """
    _require_https(discovery_url, "OIDC discovery URL")
    async with httpx.AsyncClient() as client:
        resp = await client.get(discovery_url)
        resp.raise_for_status()
    cfg = resp.json()
    _validate_issuer(discovery_url, cfg.get("issuer"))
    userinfo_url = cfg.get("userinfo_endpoint")
    if userinfo_url is not None:
        _require_https(userinfo_url, "OIDC userinfo_endpoint")
    return (
        _require_https(cfg["authorization_endpoint"], "OIDC authorization_endpoint"),
        _require_https(cfg["token_endpoint"], "OIDC token_endpoint"),
        userinfo_url,
    )


async def oauth_fetch_userinfo(
    *,
    token_url: str,
    userinfo_url: str,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    required_scopes: str | None = None,
) -> dict[str, Any]:
    """Exchange an authorization code for tokens and return the userinfo payload.

    Args:
        token_url: Provider's token endpoint.
        userinfo_url: Provider's userinfo endpoint.
        code: Authorization code received from the provider's callback.
        client_id: OAuth application client ID.
        client_secret: OAuth application client secret.
        redirect_uri: Redirect URI that was used in the authorization request.
        required_scopes: Space-separated scopes that must be present in the token
            response ``scope`` field (RFC 6749 §3.3).  Raises ``ValueError`` if
            the provider granted fewer scopes than requested.

    Returns:
        The JSON payload returned by the userinfo endpoint as a plain ``dict``.

    Raises:
        ValueError: If *token_url* or *userinfo_url* is not HTTPS (loopback
            hosts excepted), if the provider granted a different token type
            than ``bearer``, or if it did not grant all ``required_scopes``.
    """
    _require_https(token_url, "OAuth token_url")
    _require_https(userinfo_url, "OAuth userinfo_url")
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
        token_data = token_resp.json()

        if token_data.get("token_type", "bearer").lower() != "bearer":
            raise ValueError(
                f"unsupported token_type: {token_data.get('token_type')!r}"
            )

        if required_scopes is not None:
            granted = set(token_data.get("scope", "").split())
            missing = set(required_scopes.split()) - granted
            if missing:
                raise ValueError(f"provider did not grant required scopes: {missing}")

        access_token = token_data["access_token"]

        userinfo_resp = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo_resp.raise_for_status()
        return userinfo_resp.json()


def oauth_generate_state_token() -> str:
    """Generate a cryptographically random CSRF token for the OAuth ``state`` parameter."""
    return secrets.token_urlsafe(32)


def oauth_build_authorization_redirect(
    authorization_url: str,
    *,
    client_id: str,
    scopes: str,
    redirect_uri: str,
    destination: str,
    state_token: str,
) -> RedirectResponse:
    """Return an OAuth 2.0 authorization ``RedirectResponse``.

    Args:
        authorization_url: Provider's authorization endpoint.
        client_id: OAuth application client ID.
        scopes: Space-separated list of requested scopes.
        redirect_uri: URI the provider should redirect back to after authorization.
        destination: URL the user should be sent to after the full OAuth flow
            completes (embedded in ``state``).
        state_token: CSRF token generated by :func:`oauth_generate_state_token`.
            Must be stored server-side (session or signed cookie) and verified via
            :func:`oauth_decode_state` on the callback endpoint (RFC 6749 §10.12).

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
            "state": oauth_encode_state(destination, state_token),
        }
    )
    return RedirectResponse(f"{authorization_url}?{params}")


def oauth_encode_state(url: str, state_token: str) -> str:
    """Encode a destination URL and CSRF token into an OAuth ``state`` parameter.

    Args:
        url: Post-login destination URL.
        state_token: CSRF token from :func:`oauth_generate_state_token`.
    """
    payload = json.dumps({"n": state_token, "d": url}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def oauth_decode_state(
    state: str | None, *, expected_state_token: str, fallback: str
) -> str:
    """Decode and CSRF-verify an OAuth ``state`` parameter.

    Uses a constant-time comparison for the CSRF token to prevent timing attacks.

    Args:
        state: Raw ``state`` query parameter from the provider's callback.
        expected_state_token: The token stored before the authorization redirect.
            If it does not match the decoded value, ``fallback`` is returned.
        fallback: URL to return when ``state`` is absent, malformed, or fails
            CSRF verification.

    Returns:
        The destination URL embedded in ``state``, or ``fallback``.

    Important:
        **Single-use**: delete the stored token from the session immediately
        after calling this function — whether it matched or not — so that a
        captured callback URL cannot be replayed.

        **Open-redirect**: validate the returned URL against a known-good
        origin or relative-path allowlist before issuing the final redirect.
        Do not forward arbitrary URLs to ``RedirectResponse``.
    """
    if not state or state == "null":  # "null" guards against JS JSON.stringify(null)
        return fallback
    try:
        padded = state + "=" * (-len(state) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(payload, dict) or not hmac.compare_digest(
            payload.get("n", "").encode(), expected_state_token.encode()
        ):
            return fallback
        return str(payload["d"])
    except (UnicodeDecodeError, ValueError, binascii.Error, KeyError):
        return fallback
