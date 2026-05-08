"""Built-in authentication source implementations."""

from .header import APIKeyHeaderAuth
from .bearer import BearerTokenAuth
from .cookie import CookieAuth
from .multi import MultiAuth

__all__ = ["APIKeyHeaderAuth", "BearerTokenAuth", "CookieAuth", "MultiAuth"]
