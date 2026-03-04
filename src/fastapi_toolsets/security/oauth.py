"""OAuth 2.0 / OIDC helper utilities."""

import base64


def encode_oauth_state(url: str) -> str:
    """Base64url-encode a URL to embed as an OAuth ``state`` parameter."""
    return base64.urlsafe_b64encode(url.encode()).decode()


def decode_oauth_state(state: str | None, *, fallback: str) -> str:
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
