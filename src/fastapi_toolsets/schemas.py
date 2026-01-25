"""Base Pydantic schemas for API responses."""

from enum import Enum
from typing import ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ApiError",
    "ErrorResponse",
    "Pagination",
    "PaginatedResponse",
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
        Response[UserRead](data=user, message="User retrieved")
    """

    data: DataT | None = None


class ErrorResponse(BaseResponse):
    """Error response with additional description field.

    Used for error responses that need more context.
    """

    status: ResponseStatus = ResponseStatus.FAIL
    description: str | None = None
    data: None = None


class Pagination(PydanticBase):
    """Pagination metadata for list responses.

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


class PaginatedResponse(BaseResponse, Generic[DataT]):
    """Paginated API response for list endpoints.

    Example:
        PaginatedResponse[UserRead](
            data=users,
            pagination=Pagination(total_count=100, items_per_page=10, page=1, has_more=True)
        )
    """

    data: list[DataT]
    pagination: Pagination
