"""Authentication helpers for FastAPI using Security()."""

from .abc import AuthSource
from .oauth import (
    oauth_build_authorization_redirect,
    oauth_decode_state,
    oauth_encode_state,
    oauth_fetch_userinfo,
    oauth_generate_state_token,
    oauth_resolve_provider_urls,
)
from .sources import APIKeyHeaderAuth, BearerTokenAuth, CookieAuth, MultiAuth

__all__ = [
    "APIKeyHeaderAuth",
    "AuthSource",
    "BearerTokenAuth",
    "CookieAuth",
    "MultiAuth",
    "oauth_build_authorization_redirect",
    "oauth_decode_state",
    "oauth_encode_state",
    "oauth_fetch_userinfo",
    "oauth_generate_state_token",
    "oauth_resolve_provider_urls",
]
