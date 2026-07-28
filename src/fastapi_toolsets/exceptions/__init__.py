"""Standardized API exceptions and error response handlers."""

from .exceptions import (
    ApiError,
    ApiException,
    ConflictError,
    ForbiddenError,
    InvalidFacetFilterError,
    InvalidOrderFieldError,
    InvalidSearchColumnError,
    LockTimeoutError,
    NoSearchableFieldsError,
    NotFoundError,
    PoolExhaustedError,
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
    "InvalidFacetFilterError",
    "InvalidOrderFieldError",
    "InvalidSearchColumnError",
    "LockTimeoutError",
    "NoSearchableFieldsError",
    "NotFoundError",
    "PoolExhaustedError",
    "UnauthorizedError",
    "UnsupportedFacetTypeError",
    "generate_error_responses",
    "init_exceptions_handlers",
]
