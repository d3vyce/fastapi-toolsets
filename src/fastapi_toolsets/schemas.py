"""Base Pydantic schemas for API responses."""

from enum import Enum
from typing import Annotated, Any, ClassVar, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field

from .types import DataT

__all__ = [
    "ApiError",
    "CursorPagination",
    "CursorPaginatedResponse",
    "ErrorResponse",
    "OffsetPagination",
    "OffsetPaginatedResponse",
    "PaginatedResponse",
    "PaginationType",
    "PydanticBase",
    "Response",
    "ResponseStatus",
]


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


class PaginationType(str, Enum):
    """Pagination strategy selector for :meth:`.AsyncCrud.paginate`."""

    OFFSET = "offset"
    CURSOR = "cursor"


class PaginatedResponse(BaseResponse, Generic[DataT]):
    """Paginated API response for list endpoints.

    Base class and return type for endpoints that support both pagination
    strategies.  Use :class:`OffsetPaginatedResponse` or
    :class:`CursorPaginatedResponse` when the strategy is fixed.

    When used as ``PaginatedResponse[T]`` in a return annotation, subscripting
    returns ``Annotated[Union[CursorPaginatedResponse[T], OffsetPaginatedResponse[T]], Field(discriminator="pagination_type")]``
    so FastAPI emits a proper ``oneOf`` + discriminator in the OpenAPI schema.
    """

    data: list[DataT]
    pagination: OffsetPagination | CursorPagination
    pagination_type: PaginationType | None = None
    filter_attributes: dict[str, list[Any]] | None = None

    _discriminated_union_cache: ClassVar[dict[Any, Any]] = {}

    def __class_getitem__(  # type: ignore[invalid-method-override]
        cls, item: type[Any] | tuple[type[Any], ...]
    ) -> type[Any]:
        if cls is PaginatedResponse and not isinstance(item, TypeVar):
            cached = cls._discriminated_union_cache.get(item)
            if cached is None:
                cached = Annotated[
                    Union[CursorPaginatedResponse[item], OffsetPaginatedResponse[item]],  # type: ignore[invalid-type-form]
                    Field(discriminator="pagination_type"),
                ]
                cls._discriminated_union_cache[item] = cached
            return cached  # type: ignore[invalid-return-type]
        return super().__class_getitem__(item)


class OffsetPaginatedResponse(PaginatedResponse[DataT]):
    """Paginated response with typed offset-based pagination metadata.

    The ``pagination_type`` field is always ``"offset"`` and acts as a
    discriminator, allowing frontend clients to narrow the union type returned
    by a unified ``paginate()`` endpoint.
    """

    pagination: OffsetPagination
    pagination_type: Literal[PaginationType.OFFSET] = PaginationType.OFFSET


class CursorPaginatedResponse(PaginatedResponse[DataT]):
    """Paginated response with typed cursor-based pagination metadata.

    The ``pagination_type`` field is always ``"cursor"`` and acts as a
    discriminator, allowing frontend clients to narrow the union type returned
    by a unified ``paginate()`` endpoint.
    """

    pagination: CursorPagination
    pagination_type: Literal[PaginationType.CURSOR] = PaginationType.CURSOR
