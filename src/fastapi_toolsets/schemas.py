"""Base Pydantic schemas for API responses."""

from enum import Enum
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ApiError",
    "CursorPagination",
    "ErrorResponse",
    "OffsetPagination",
    "Pagination",
    "PaginatedResponse",
    "PydanticBase",
    "Response",
    "ResponseStatus",
]

DataT = TypeVar("DataT")


class PydanticBase(BaseModel):
    """Base class for all Pydantic models with common configuration."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True,
        validate_assignment=True,
    )


class ResponseStatus(str, Enum):
    """Standard API response status."""

    SUCCESS = "SUCCESS"
    FAIL = "FAIL"


class ApiError(PydanticBase):
    """Structured API error definition.

    Used to define standard error responses with consistent format.

    Attributes:
        code: HTTP status code
        msg: Short error message
        desc: Detailed error description
        err_code: Application-specific error code (e.g., "AUTH-401")
    """

    code: int
    msg: str
    desc: str
    err_code: str
    data: Any | None = None


class BaseResponse(PydanticBase):
    """Base response structure for all API responses.

    Attributes:
        status: SUCCESS or FAIL
        message: Human-readable message
        error_code: Error code if status is FAIL, None otherwise
    """

    status: ResponseStatus = ResponseStatus.SUCCESS
    message: str = "Success"
    error_code: str | None = None


class Response(BaseResponse, Generic[DataT]):
    """Generic API response with data payload.

    Example:
        ```python
        Response[UserRead](data=user, message="User retrieved")
        ```
    """

    data: DataT | None = None


class ErrorResponse(BaseResponse):
    """Error response with additional description field.

    Used for error responses that need more context.
    """

    status: ResponseStatus = ResponseStatus.FAIL
    description: str | None = None
    data: Any | None = None


class OffsetPagination(PydanticBase):
    """Pagination metadata for offset-based list responses.

    Attributes:
        total_count: Total number of items across all pages
        items_per_page: Number of items per page
        page: Current page number (1-indexed)
        has_more: Whether there are more pages
    """

    total_count: int
    items_per_page: int
    page: int
    has_more: bool


# Backward-compatible - will be removed in v2.0
Pagination = OffsetPagination


class CursorPagination(PydanticBase):
    """Pagination metadata for cursor-based list responses.

    Attributes:
        next_cursor: Encoded cursor for the next page, or None on the last page.
        prev_cursor: Encoded cursor for the previous page, or None on the first page.
        items_per_page: Number of items requested per page.
        has_more: Whether there is at least one more page after this one.
    """

    next_cursor: str | None
    prev_cursor: str | None = None
    items_per_page: int
    has_more: bool


class PaginatedResponse(BaseResponse, Generic[DataT]):
    """Paginated API response for list endpoints."""

    data: list[DataT]
    pagination: OffsetPagination | CursorPagination
