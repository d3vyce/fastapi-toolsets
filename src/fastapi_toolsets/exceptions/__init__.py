"""Standardized API exceptions and error response handlers."""

from .exceptions import (
    ApiError,
    ApiException,
    ConflictError,
    ForbiddenError,
    InvalidFacetFilterError,
    InvalidOrderFieldError,
    NoSearchableFieldsError,
    NotFoundError,
    UnauthorizedError,
    UnsupportedFacetTypeError,
    generate_error_responses,
)
from .handler import init_exceptions_handlers

__all__ = [
    "ApiError",
    "ApiException",
    "ConflictError",
    "ForbiddenError",
    "generate_error_responses",
    "init_exceptions_handlers",
    "InvalidFacetFilterError",
    "InvalidOrderFieldError",
    "NoSearchableFieldsError",
    "NotFoundError",
    "UnauthorizedError",
    "UnsupportedFacetTypeError",
]
